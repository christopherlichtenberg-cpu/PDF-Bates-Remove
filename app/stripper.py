"""Core PDF OCR text-layer stripping engine, built on PyMuPDF (fitz).

IMPORTANT (revised twice after real-world testing):

  1. First revision: "Full strip" originally only removed text marked with
     the PDF invisible render mode (Tr 3). Real test files showed some
     OCR/eDiscovery tools instead render the text layer normally but
     colored white - visually invisible but not "invisible" in PDF terms -
     so that detection missed it entirely.
  2. Second revision: switching Full Strip to "redact every text object on
     the page" fixed that, but broke files where the human-visible content
     is ITSELF made of text glyphs (e.g. a vector/ClearScan-style layer, or
     genuine typed content) rather than a separate scanned image underneath
     the text. Redacting all text in that case deletes what a person sees,
     not just what's searchable.

  The only approach that satisfies "a human sees the same thing, but it's
  not searchable" regardless of how the original PDF was constructed is to
  RENDER a page to a flat raster image and rebuild it from just that image,
  discarding all original text/vector objects - guaranteeing pixel-for-pixel
  visual fidelity with zero surviving text.

  A per-page shortcut was tried where pages with a large background image
  ("this looks like a real scan") were redacted instead of flattened, to
  avoid the size/speed cost. Deliberately reverted: that heuristic can be
  wrong (an image can dominate the page area while the actual meaningful
  visible content is still the text on top of/beside it), and getting it
  wrong means silently deleting real content again - the exact failure this
  module has already hit twice. So every page with any text is always
  flattened, no exceptions. Pages with zero text are already non-searchable
  and are copied through unchanged (free - no risk, nothing to decide).

  File size/speed from flattening is instead expected to be handled in
  Stage 2 by running the output through Adobe Acrobat's PDF Optimizer (a
  distinct feature from "Recognize Text"/OCR, purpose-built for
  recompressing scanned-image PDFs without visible quality loss) rather
  than by us guessing at page classification.

  Regex mode is unchanged: it targets and redacts only text matching the
  user's pattern (any render mode/color), which is a deliberate, surgical
  removal (e.g. redacting a specific ID/Bates number) rather than a
  everything-becomes-non-searchable operation, so it's expected to remove
  matched text from view too.

  Image-region mode (opt-in, see ``image_bates``): for pages where the Bates
  stamp is burned into the page image, OCRs a user-drawn region, and
  redacts matches down to the image pixels. Unlike the two modes above it
  does not require the PDF to have any text layer.

Technique notes (validated against the installed PyMuPDF 1.28.0 / MuPDF 1.29.0):

  - ``page.get_pixmap()`` renders a page to a raster image at a chosen DPI;
    rebuilding a page from just that image guarantees no text objects
    survive.
  - ``page.get_texttrace()`` gives per-span character-level bboxes, which
    regex mode (and the "has any text" check) needs.
  - Redaction removes real content, but its defaults are destructive to
    scanned images and vector graphics. Regex mode always passes
    ``images=PDF_REDACT_IMAGE_NONE, graphics=PDF_REDACT_LINE_ART_NONE`` so a
    redaction rectangle never touches the scan image or vector graphics -
    only text. Image-region mode deliberately uses
    ``PDF_REDACT_IMAGE_PIXELS`` instead, limited to the matched boxes.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pymupdf as fitz

from app.image_bates import OCR_DPI, apply_image_region_strip, format_page_list

STRIP_MODE_FULL = "full"
STRIP_MODE_REGEX = "regex"
STRIP_MODE_IMAGE_REGION = "image_region"

DEFAULT_FLATTEN_DPI = 300

STATUS_STRIPPED = "stripped"
STATUS_PARTIAL = "partial"
STATUS_NO_OCR_LAYER = "no_ocr_layer"
STATUS_NO_MATCHES = "no_matches"
STATUS_SKIPPED_ENCRYPTED = "skipped_encrypted"
STATUS_SKIPPED_CORRUPT = "skipped_corrupt"
STATUS_SKIPPED_LOCKED = "skipped_locked"
STATUS_ERROR = "error"


@dataclass
class StripResult:
    status: str
    message: str
    output_path: Path | None = None


class InvalidRegexError(ValueError):
    pass


def compile_pattern(pattern: str) -> re.Pattern:
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise InvalidRegexError(str(exc)) from exc


def _span_text_and_chars(span: dict) -> tuple[str, list]:
    chars = span.get("chars", ())
    text = "".join(chr(c[0]) for c in chars)
    return text, chars


def _doc_has_any_text(doc: "fitz.Document") -> bool:
    for page in doc:
        if page.get_texttrace():
            return True
    return False


def _flatten_page_to_image(result_doc: "fitz.Document", page: "fitz.Page", matrix: "fitz.Matrix") -> None:
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    img_bytes = pix.tobytes("png")
    new_page = result_doc.new_page(width=page.rect.width, height=page.rect.height)
    new_page.insert_image(new_page.rect, stream=img_bytes)


def _apply_full_strip(doc: "fitz.Document", dpi: int) -> "fitz.Document":
    """Per-page: pages with no text at all are already non-searchable and
    are copied through unchanged (free, zero risk). Every page with any text
    is flattened to an image."""
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    result = fitz.open()
    for idx, page in enumerate(doc):
        if page.get_texttrace():
            _flatten_page_to_image(result, page, matrix)
        else:
            result.insert_pdf(doc, from_page=idx, to_page=idx)
    return result


def _apply_regex_strip(doc: "fitz.Document", pattern: re.Pattern) -> int:
    """Redacts text matching pattern, regardless of render mode/color. Returns match count."""
    total_matches = 0
    for page in doc:
        rects_to_redact = []
        for span in page.get_texttrace():
            text, chars = _span_text_and_chars(span)
            if not text:
                continue
            for match in pattern.finditer(text):
                if match.start() == match.end():
                    continue
                matched_chars = chars[match.start():match.end()]
                if not matched_chars:
                    continue
                x0 = min(c[3][0] for c in matched_chars)
                y0 = min(c[3][1] for c in matched_chars)
                x1 = max(c[3][2] for c in matched_chars)
                y1 = max(c[3][3] for c in matched_chars)
                rects_to_redact.append(fitz.Rect(x0, y0, x1, y1))
                total_matches += 1
        if not rects_to_redact:
            continue
        for rect in rects_to_redact:
            page.add_redact_annot(rect)
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE,
        )
    return total_matches


def strip_pdf(
    input_path: Path,
    output_path: Path,
    mode: str,
    regex_pattern: str | None = None,
    flatten_dpi: int = DEFAULT_FLATTEN_DPI,
    image_region: list[float] | None = None,
    ocr_dpi: int = OCR_DPI,
) -> StripResult:
    """Never raises for per-file problems (bad PDF, locked output, etc.) -
    returns a StripResult so the batch can continue. Only raises
    InvalidRegexError, which is a whole-job configuration problem."""
    compiled_pattern = None
    if mode in (STRIP_MODE_REGEX, STRIP_MODE_IMAGE_REGION):
        if not regex_pattern:
            raise InvalidRegexError("No regex pattern provided")
        compiled_pattern = compile_pattern(regex_pattern)

    try:
        doc = fitz.open(str(input_path))
    except Exception:
        return StripResult(STATUS_SKIPPED_CORRUPT, "Corrupt or unreadable PDF")

    try:
        if not doc.is_pdf or doc.page_count == 0:
            return StripResult(STATUS_SKIPPED_CORRUPT, "Corrupt or unreadable PDF")

        if doc.is_encrypted or doc.needs_pass:
            return StripResult(STATUS_SKIPPED_ENCRYPTED, "PDF is encrypted/locked")

        partial_message = None
        if mode == STRIP_MODE_IMAGE_REGION:
            # No "has any text" short-circuit here: image-only pages are
            # exactly what this mode is for.
            if not image_region:
                return StripResult(STATUS_ERROR, "No image search region selected")
            try:
                match_count, pages_without_match = apply_image_region_strip(
                    doc, compiled_pattern, image_region, dpi=ocr_dpi
                )
            except Exception as exc:
                return StripResult(STATUS_ERROR, f"OCR/redaction failed: {exc}")
            if match_count == 0:
                _copy_unchanged(input_path, output_path)
                return StripResult(
                    STATUS_NO_MATCHES,
                    "no pattern matches found in the selected region",
                    output_path,
                )
            if pages_without_match:
                partial_message = (
                    f"removed {match_count} match(es); NO match on page(s) "
                    f"{format_page_list(pages_without_match)} of {doc.page_count} - check these"
                )
            else:
                success_message = f"removed {match_count} match(es) from page images"
        else:
            if not _doc_has_any_text(doc):
                _copy_unchanged(input_path, output_path)
                return StripResult(STATUS_NO_OCR_LAYER, "no OCR layer found", output_path)

            if mode == STRIP_MODE_FULL:
                stripped = _apply_full_strip(doc, flatten_dpi)
                doc.close()
                doc = stripped
            else:
                match_count = _apply_regex_strip(doc, compiled_pattern)
                if match_count == 0:
                    _copy_unchanged(input_path, output_path)
                    return StripResult(
                        STATUS_NO_MATCHES,
                        "OCR layer present but no text matched the pattern",
                        output_path,
                    )
            success_message = "stripped successfully"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if output_path == input_path:
                # Can't save over a file PyMuPDF still has open: write a
                # temp file alongside, then swap it in.
                tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
                doc.save(str(tmp_path), garbage=4, deflate=True)
                doc.close()
                tmp_path.replace(output_path)
            else:
                doc.save(str(output_path), garbage=4, deflate=True)
        except (OSError, PermissionError) as exc:
            return StripResult(STATUS_SKIPPED_LOCKED, f"Could not write output (file open elsewhere?): {exc}")

        if partial_message:
            return StripResult(STATUS_PARTIAL, partial_message, output_path)
        return StripResult(STATUS_STRIPPED, success_message, output_path)
    finally:
        if not doc.is_closed:
            doc.close()


def _copy_unchanged(input_path: Path, output_path: Path) -> None:
    if input_path == output_path:
        return
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(input_path, output_path)
