"""Removal of burned-in (image) Bates stamps via OCR inside a user-drawn region.

Why this exists: the standard modes in ``stripper`` only ever act on PDF
*text objects*. A produced page that is a single scanned/rendered image with
the Bates number burned into the pixels has no text object for the stamp, so
regex mode either finds nothing ("no OCR layer found") or removes only the
invisible OCR copy of the number while the visible stamp stays in the image.

This mode is opt-in and intended as a second pass over the files the simpler
run couldn't fix:

  1. The user draws one search region on a preview page (stored as fractions
     of the page box so it applies to any page size, e.g. "bottom-right
     corner").
  2. For each page, only that region is rendered and OCR'd (Tesseract,
     compiled into PyMuPDF - just needs ``tessdata/eng.traineddata``).
  3. OCR words are matched against the user's regex; each match's box (plus
     a small pad) is redacted with ``PDF_REDACT_IMAGE_PIXELS``, which
     rewrites the image pixels under the box to white - the stamp is
     actually removed from the image data, not just covered by a box.
  4. Any text-layer (OCR ghost) matches for the same pattern anywhere on the
     page are redacted the same way, so pages with a hidden OCR copy of the
     number are fully cleaned in the same pass.

Nothing outside a matched box is touched: if OCR doesn't find the pattern on
a page, that page is left as-is and reported, rather than blanking the whole
region on a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf as fitz

from app.paths import get_tessdata_dir

OCR_LANGUAGE = "eng"
OCR_DPI = 300
# Points added on every side of a matched box before redacting, so
# anti-aliased glyph edges and slightly-tight OCR boxes don't leave residue.
REDACT_PAD_PT = 2.0


class OcrUnavailableError(RuntimeError):
    pass


@dataclass
class RegionMatch:
    rect: fitz.Rect  # displayed (rotated) page space
    text: str
    source: str  # "ocr" or "text_layer"


@dataclass
class PageScanResult:
    page_number: int  # 1-based
    matches: list[RegionMatch] = field(default_factory=list)
    ocr_text: str = ""


def check_ocr_available(tessdata: Path | None = None) -> Path:
    tessdata = tessdata or get_tessdata_dir()
    if not (tessdata / f"{OCR_LANGUAGE}.traineddata").is_file():
        raise OcrUnavailableError(
            f"OCR language data not found: {tessdata / (OCR_LANGUAGE + '.traineddata')}"
        )
    return tessdata


def region_to_rect(page: "fitz.Page", region: list[float] | tuple[float, ...]) -> "fitz.Rect":
    """Fractions [x0, y0, x1, y1] of the page box -> page-space Rect."""
    r = page.rect
    x0, y0, x1, y1 = region
    rect = fitz.Rect(
        r.x0 + x0 * r.width,
        r.y0 + y0 * r.height,
        r.x0 + x1 * r.width,
        r.y0 + y1 * r.height,
    )
    return rect & r


def _ocr_words(page: "fitz.Page", clip: "fitz.Rect", tessdata: Path, dpi: int) -> list[tuple]:
    """OCR just ``clip`` and return words as (Rect in page space, text, block, line)."""
    pix = page.get_pixmap(clip=clip, dpi=dpi, colorspace=fitz.csRGB, alpha=False)
    ocr_pdf = fitz.open("pdf", pix.pdfocr_tobytes(language=OCR_LANGUAGE, tessdata=str(tessdata)))
    try:
        ocr_page = ocr_pdf[0]
        sx = clip.width / ocr_page.rect.width
        sy = clip.height / ocr_page.rect.height
        words = []
        for x0, y0, x1, y1, text, block, line, _ in ocr_page.get_text("words"):
            rect = fitz.Rect(
                clip.x0 + x0 * sx, clip.y0 + y0 * sy, clip.x0 + x1 * sx, clip.y0 + y1 * sy
            )
            words.append((rect, text, block, line))
        return words
    finally:
        ocr_pdf.close()


def _match_words(words: list[tuple], pattern: re.Pattern) -> list[RegionMatch]:
    """Regex over each OCR line. Matched both with single spaces between words
    and with spaces removed, since OCR often splits a stamp like
    "ABC.001.002.0345" into "ABC. 001.002. 0345"."""
    lines: dict[tuple, list[tuple]] = {}
    for w in words:
        lines.setdefault((w[2], w[3]), []).append(w)

    matches: list[RegionMatch] = []
    for line_words in lines.values():
        seen: set[tuple[int, ...]] = set()
        for sep in (" ", ""):
            text = ""
            owner: list[int] = []  # char index -> word index
            for i, w in enumerate(line_words):
                if i and sep:
                    text += sep
                    owner.append(-1)
                text += w[1]
                owner.extend([i] * len(w[1]))
            for m in pattern.finditer(text):
                if m.start() == m.end():
                    continue
                idxs = sorted({owner[c] for c in range(m.start(), m.end()) if owner[c] >= 0})
                key = tuple(idxs)
                if not idxs or key in seen:
                    continue
                seen.add(key)
                rect = fitz.Rect()
                for i in idxs:
                    rect |= line_words[i][0]
                matches.append(RegionMatch(rect, m.group(), "ocr"))
    return matches


def _text_layer_matches(page: "fitz.Page", pattern: re.Pattern) -> list[RegionMatch]:
    # texttrace boxes are in unrotated page space; convert to displayed
    # space so every RegionMatch uses the same coordinates as the OCR boxes.
    rotate = page.rotation_matrix
    matches = []
    for span in page.get_texttrace():
        chars = span.get("chars", ())
        text = "".join(chr(c[0]) for c in chars)
        for m in pattern.finditer(text):
            if m.start() == m.end():
                continue
            mc = chars[m.start():m.end()]
            if not mc:
                continue
            rect = fitz.Rect(
                min(c[3][0] for c in mc),
                min(c[3][1] for c in mc),
                max(c[3][2] for c in mc),
                max(c[3][3] for c in mc),
            )
            matches.append(RegionMatch(rect * rotate, m.group(), "text_layer"))
    return matches


def scan_page(
    page: "fitz.Page",
    pattern: re.Pattern,
    region: list[float],
    tessdata: Path | None = None,
    dpi: int = OCR_DPI,
    include_text_layer: bool = True,
) -> PageScanResult:
    """Find pattern matches on one page without modifying it (also used by
    the region dialog's "Test OCR" preview)."""
    tessdata = check_ocr_available(tessdata)
    result = PageScanResult(page.number + 1)
    clip = region_to_rect(page, region)
    if clip.is_empty:
        return result
    words = _ocr_words(page, clip, tessdata, dpi)
    result.ocr_text = " ".join(w[1] for w in words)
    result.matches = _match_words(words, pattern)
    if include_text_layer:
        result.matches += _text_layer_matches(page, pattern)
    return result


def redact_matches(page: "fitz.Page", matches: list[RegionMatch]) -> None:
    # Matches are in displayed (rotated) page space - what the user drew the
    # region on and what OCR read upright. Redaction annots take unrotated
    # coordinates, so map back for pages with /Rotate set.
    derotate = page.derotation_matrix
    bounds = page.rect * derotate
    for match in matches:
        rect = fitz.Rect(match.rect)
        rect.x0 -= REDACT_PAD_PT
        rect.y0 -= REDACT_PAD_PT
        rect.x1 += REDACT_PAD_PT
        rect.y1 += REDACT_PAD_PT
        page.add_redact_annot((rect * derotate) & bounds, fill=(1, 1, 1))
    page.apply_redactions(
        images=fitz.PDF_REDACT_IMAGE_PIXELS,
        graphics=fitz.PDF_REDACT_LINE_ART_NONE,
        text=fitz.PDF_REDACT_TEXT_REMOVE,
    )


def apply_image_region_strip(
    doc: "fitz.Document",
    pattern: re.Pattern,
    region: list[float],
    tessdata: Path | None = None,
    dpi: int = OCR_DPI,
) -> tuple[int, list[int]]:
    """Redacts every match on every page. Returns (match count, 1-based page
    numbers where nothing matched)."""
    tessdata = check_ocr_available(tessdata)
    total = 0
    pages_without_match = []
    for page in doc:
        scan = scan_page(page, pattern, region, tessdata, dpi)
        if not scan.matches:
            pages_without_match.append(scan.page_number)
            continue
        redact_matches(page, scan.matches)
        total += len(scan.matches)
    return total, pages_without_match


def format_page_list(pages: list[int], limit: int = 15) -> str:
    shown = ", ".join(str(p) for p in pages[:limit])
    if len(pages) > limit:
        shown += f" (+{len(pages) - limit} more)"
    return shown
