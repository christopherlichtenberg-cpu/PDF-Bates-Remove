"""Tests for image-region OCR mode, plus regression checks that the original
modes still behave as in v1.0. Fixtures are built on the fly: a page that is
one raster image with a Bates number burned into the pixels."""
from __future__ import annotations

import io
import re
from pathlib import Path

import pymupdf as fitz
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.image_bates import _match_words, scan_page
from app.stripper import (
    STATUS_NO_MATCHES,
    STATUS_NO_OCR_LAYER,
    STATUS_PARTIAL,
    STATUS_STRIPPED,
    STRIP_MODE_FULL,
    STRIP_MODE_IMAGE_REGION,
    STRIP_MODE_REGEX,
    strip_pdf,
)

PATTERN = r"ABC\.\d{3}\.\d{3}\.\d{4}"
BODY = "Body text of the scanned letter."
# Bottom-right corner, as fractions of the page.
REGION = [0.55, 0.9, 1.0, 1.0]
STAMP_PX = (1150, 2100)  # top-left of the stamp in the 1700x2200 page image


def _page_image(bates: str | None) -> bytes:
    img = Image.new("L", (1700, 2200), 255)
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=32)
    draw.text((150, 200), BODY, fill=0, font=font)
    if bates:
        draw.text(STAMP_PX, bates, fill=0, font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_pdf(path: Path, bates_per_page: list[str | None], ghost_text: bool = False, rotate: int = 0) -> Path:
    doc = fitz.open()
    for bates in bates_per_page:
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, stream=_page_image(bates))
        if ghost_text and bates:
            # Invisible OCR layer (render mode 3) over the stamp, as OCR tools produce.
            page.insert_text((414, 768), bates, fontsize=11, render_mode=3)
        if rotate:
            page.set_rotation(rotate)
    doc.save(str(path))
    return path


def _stamp_ink(page: fitz.Page) -> int:
    """Dark pixels in the (unrotated) stamp area of the page's image."""
    xref = page.get_images()[0][0]
    pix = fitz.Pixmap(page.parent, xref)
    if pix.n > 1:
        pix = fitz.Pixmap(fitz.csGRAY, pix)
    x0, y0 = STAMP_PX
    samples = pix.samples
    dark = 0
    for y in range(y0, min(y0 + 45, pix.height)):
        row = samples[y * pix.stride:(y + 1) * pix.stride]
        dark += sum(1 for v in row[x0:x0 + 420] if v < 128)
    return dark


def _body_ink(page: fitz.Page) -> int:
    xref = page.get_images()[0][0]
    pix = fitz.Pixmap(page.parent, xref)
    if pix.n > 1:
        pix = fitz.Pixmap(fitz.csGRAY, pix)
    samples = pix.samples
    return sum(
        1
        for y in range(200, 245)
        for v in samples[y * pix.stride + 150:y * pix.stride + 700]
        if v < 128
    )


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    return tmp_path


def test_image_only_pdf_stamp_removed_from_pixels(tmp: Path) -> None:
    src = _make_pdf(tmp / "in.pdf", ["ABC.001.002.0345"])
    out = tmp / "out.pdf"
    before = fitz.open(str(src))
    ink_before, body_before = _stamp_ink(before[0]), _body_ink(before[0])
    assert ink_before > 0

    result = strip_pdf(src, out, STRIP_MODE_IMAGE_REGION, PATTERN, image_region=REGION)

    assert result.status == STATUS_STRIPPED, result.message
    doc = fitz.open(str(out))
    assert _stamp_ink(doc[0]) == 0
    assert _body_ink(doc[0]) == body_before  # nothing outside the match touched
    assert not scan_page(doc[0], re.compile(PATTERN), REGION).matches


def test_image_with_ocr_ghost_layer_removes_both(tmp: Path) -> None:
    src = _make_pdf(tmp / "in.pdf", ["ABC.001.002.0346"], ghost_text=True)
    out = tmp / "out.pdf"

    result = strip_pdf(src, out, STRIP_MODE_IMAGE_REGION, PATTERN, image_region=REGION)

    assert result.status == STATUS_STRIPPED, result.message
    doc = fitz.open(str(out))
    assert "ABC" not in doc[0].get_text()
    assert _stamp_ink(doc[0]) == 0


def test_region_not_over_stamp_reports_no_matches_and_copies(tmp: Path) -> None:
    src = _make_pdf(tmp / "in.pdf", ["ABC.001.002.0347"])
    out = tmp / "out.pdf"

    result = strip_pdf(src, out, STRIP_MODE_IMAGE_REGION, PATTERN, image_region=[0.0, 0.0, 0.3, 0.05])

    assert result.status == STATUS_NO_MATCHES
    assert out.read_bytes() == src.read_bytes()


def test_page_without_stamp_reported_as_partial(tmp: Path) -> None:
    src = _make_pdf(tmp / "in.pdf", ["ABC.001.002.0348", None, "ABC.001.002.0350"])
    out = tmp / "out.pdf"

    result = strip_pdf(src, out, STRIP_MODE_IMAGE_REGION, PATTERN, image_region=REGION)

    assert result.status == STATUS_PARTIAL
    assert "page(s) 2 of 3" in result.message
    doc = fitz.open(str(out))
    assert _stamp_ink(doc[0]) == 0 and _stamp_ink(doc[2]) == 0


def test_overwrite_in_place(tmp: Path) -> None:
    src = _make_pdf(tmp / "in.pdf", ["ABC.001.002.0351"])

    result = strip_pdf(src, src, STRIP_MODE_IMAGE_REGION, PATTERN, image_region=REGION)

    assert result.status == STATUS_STRIPPED
    assert _stamp_ink(fitz.open(str(src))[0]) == 0
    assert not (tmp / "in.pdf.tmp").exists()


@pytest.mark.parametrize("rotation", [90, 180])
def test_rotated_page(tmp: Path, rotation: int) -> None:
    # Page stored sideways/upside-down with /Rotate set so it *displays*
    # upright - the stamp is bottom-right on screen, where the user draws
    # the region. Includes a ghost OCR layer, which lives in unrotated space.
    upright = Image.open(io.BytesIO(_page_image("ABC.001.002.0352")))
    stored = upright.rotate(rotation, expand=True)
    buf = io.BytesIO()
    stored.save(buf, format="PNG")
    doc = fitz.open()
    page = doc.new_page(width=stored.width * 0.36, height=stored.height * 0.36)
    page.insert_image(page.rect, stream=buf.getvalue())
    page.set_rotation(rotation)
    page.insert_text(fitz.Point(414, 768) * page.derotation_matrix, "ABC.001.002.0352",
                     fontsize=11, render_mode=3, rotate=rotation)
    src = tmp / "in.pdf"
    doc.save(str(src))
    out = tmp / "out.pdf"

    result = strip_pdf(src, out, STRIP_MODE_IMAGE_REGION, PATTERN, image_region=REGION)

    assert result.status == STATUS_STRIPPED, result.message
    page = fitz.open(str(out))[0]
    assert "ABC" not in page.get_text()
    region_view = page.get_pixmap(clip=fitz.Rect(330, 712, 612, 792), dpi=150, colorspace=fitz.csGRAY)
    assert min(region_view.samples) > 200  # no stamp ink left on screen
    assert _body_visible(page)


def _body_visible(page: fitz.Page) -> bool:
    view = page.get_pixmap(clip=fitz.Rect(50, 60, 260, 90), dpi=150, colorspace=fitz.csGRAY)
    return min(view.samples) < 100


def test_match_words_handles_ocr_split_tokens() -> None:
    r = fitz.Rect
    words = [
        (r(0, 0, 30, 10), "Page", 0, 0),
        (r(40, 0, 60, 10), "ABC.", 0, 0),
        (r(62, 0, 120, 10), "001.002.", 0, 0),
        (r(122, 0, 150, 10), "0345", 0, 0),
    ]
    matches = _match_words(words, re.compile(PATTERN))
    assert len(matches) == 1
    assert matches[0].rect == r(40, 0, 150, 10)


# ----- original modes unchanged ------------------------------------------------


def test_regex_mode_still_skips_image_only(tmp: Path) -> None:
    src = _make_pdf(tmp / "in.pdf", ["ABC.001.002.0353"])
    result = strip_pdf(src, tmp / "out.pdf", STRIP_MODE_REGEX, PATTERN)
    assert result.status == STATUS_NO_OCR_LAYER


def test_regex_mode_removes_ghost_text_only(tmp: Path) -> None:
    src = _make_pdf(tmp / "in.pdf", ["ABC.001.002.0354"], ghost_text=True)
    out = tmp / "out.pdf"
    result = strip_pdf(src, out, STRIP_MODE_REGEX, PATTERN)
    assert result.status == STATUS_STRIPPED
    doc = fitz.open(str(out))
    assert "ABC" not in doc[0].get_text()
    assert _stamp_ink(doc[0]) > 0  # v1.0 behaviour: image untouched


def test_full_mode_flattens_text_pages(tmp: Path) -> None:
    src = _make_pdf(tmp / "in.pdf", ["ABC.001.002.0355"], ghost_text=True)
    out = tmp / "out.pdf"
    result = strip_pdf(src, out, STRIP_MODE_FULL)
    assert result.status == STATUS_STRIPPED
    assert fitz.open(str(out))[0].get_text().strip() == ""
