# PDF OCR Layer Tool

Windows desktop tool (Python + Tkinter + PyMuPDF) that removes Bates numbers
and/or the OCR text layer from produced PDFs. The user-facing guide is
[`READ ME FIRST.txt`](READ%20ME%20FIRST.txt).

The v1.0 build only exists as the PyInstaller executable in
`PDF Text Removal.zip.001-003`. The source in this repo was reconstructed from
that executable's bytecode. Its behaviour matches v1.0, except that the unused
Acrobat UI-automation code in `ocr_adobe.py` was not carried over. v1.1 adds
image-region OCR mode.

## Strip modes

| Mode | What it removes | Image-only pages |
|---|---|---|
| `full` | Every page with text is flattened to a 300 DPI image | Copied unchanged ("no OCR layer found") |
| `regex` | Text objects matching the pattern (images never touched) | Copied unchanged ("no OCR layer found") |
| `image_region` *(new, opt-in)* | Pattern matches found by OCR inside a user-drawn region, erased from the image pixels, plus matching text-layer text | Handled |

`image_region` (`app/image_bates.py`):

1. The region is stored as page fractions `[x0, y0, x1, y1]` in `config.json` (`image_region`), relative to the displayed (rotated) page.
2. For each page, only the region is rendered (300 DPI) and OCR'd with the Tesseract engine built into PyMuPDF, using `tessdata/eng.traineddata`. No Tesseract install is needed.
3. The regex runs against each OCR line, both with and without the spaces between words, because OCR often splits stamps like `ABC. 001.002. 0345`.
4. Each match box is padded by 2 pt and redacted with `PDF_REDACT_IMAGE_PIXELS`. This rewrites the image pixels to white; it does not draw a box over them. Vector graphics are left alone.
5. Pages with no match are left as-is and listed in a warning in the log. The region is never blanked on a guess.

## Develop / test

```
py -m pip install -r requirements-dev.txt
py main.py              # run from source
py -m pytest tests      # builds fixture PDFs on the fly
```

## Build the .exe (Windows)

```
build.bat               # -> dist\PDF OCR Layer Tool.exe + READ ME FIRST.txt
```

`tessdata/eng.traineddata` is `tessdata_fast` English from
[tesseract-ocr/tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast)
(Apache-2.0). It is bundled into the exe by `--add-data`.
