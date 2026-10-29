"""'Select Region' dialog for image-region OCR mode.

Shows a rendered preview of a sample PDF page; the user drags a rectangle
over where the burned-in Bates stamp sits. The region is stored as
fractions of the page box so the same corner/strip applies to every page and
file regardless of page size. A "Test OCR" button runs the real OCR + regex
on the previewed page and draws what would be removed, without modifying
the file.
"""
from __future__ import annotations

import base64
import re
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pymupdf as fitz

from app.image_bates import OcrUnavailableError, scan_page
from app.logger import format_region

_CANVAS_W = 540
_CANVAS_H = 700
_MIN_DRAG_PX = 6
_REGION_COLOR = "#cf222e"
_MATCH_COLOR = "#1a7f37"


class RegionSelectDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        sample_files: list[Path],
        pattern_var: tk.StringVar,
        initial_region: list[float] | None,
    ) -> None:
        super().__init__(master)
        self.pattern_var = pattern_var
        self.region: list[float] | None = list(initial_region) if initial_region else None
        self.saved = False

        self._sample_files = sample_files
        self._doc: fitz.Document | None = None
        self._page_index = 0
        self._photo: tk.PhotoImage | None = None
        self._img_w = 0
        self._img_h = 0
        self._drag_start: tuple[float, float] | None = None

        self.title("Select Bates Search Region")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

        self._build_ui()

        if sample_files:
            self._open_sample(sample_files[0])

        self.grab_set()
        self.lift()
        self.focus_force()
        self.update_idletasks()
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 4
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.columnconfigure(0, weight=1)

        file_row = ttk.Frame(outer)
        file_row.grid(row=0, column=0, sticky="we")
        file_row.columnconfigure(1, weight=1)
        ttk.Label(file_row, text="Sample PDF:").grid(row=0, column=0, sticky="w")
        self.sample_var = tk.StringVar()
        self.sample_combo = ttk.Combobox(
            file_row,
            textvariable=self.sample_var,
            values=[p.name for p in self._sample_files],
            state="readonly",
            width=48,
        )
        self.sample_combo.grid(row=0, column=1, sticky="we", padx=6)
        self.sample_combo.bind("<<ComboboxSelected>>", self._on_sample_selected)
        ttk.Button(file_row, text="Browse...", command=self._browse_sample).grid(row=0, column=2)

        nav_row = ttk.Frame(outer)
        nav_row.grid(row=1, column=0, sticky="we", pady=(8, 4))
        self.prev_btn = ttk.Button(nav_row, text="◀ Prev", width=8, command=lambda: self._step_page(-1))
        self.prev_btn.pack(side="left")
        self.page_var = tk.StringVar(value="No file loaded")
        ttk.Label(nav_row, textvariable=self.page_var, width=18, anchor="center").pack(side="left", padx=6)
        self.next_btn = ttk.Button(nav_row, text="Next ▶", width=8, command=lambda: self._step_page(1))
        self.next_btn.pack(side="left")
        self.region_var = tk.StringVar(value=format_region(self.region))
        ttk.Label(nav_row, textvariable=self.region_var, foreground="#555555").pack(side="right")

        ttk.Label(
            outer,
            text="Drag on the page to draw the area where the Bates stamp appears. Only this "
            "area is OCR'd, and only text inside it that matches your pattern is removed. "
            "Draw it a little larger than the stamp, as its position can shift between pages.",
            wraplength=_CANVAS_W,
            justify="left",
            foreground="#9a6700",
        ).grid(row=2, column=0, sticky="w", pady=(0, 6))

        self.canvas = tk.Canvas(
            outer, width=_CANVAS_W, height=_CANVAS_H, background="#808080", highlightthickness=1,
            cursor="crosshair",
        )
        self.canvas.grid(row=3, column=0)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        test_row = ttk.Frame(outer)
        test_row.grid(row=4, column=0, sticky="we", pady=(8, 0))
        ttk.Button(test_row, text="Test OCR on this page", command=self._test_ocr).pack(side="left")
        ttk.Label(test_row, text="(uses the pattern from the main window)", foreground="#888888").pack(
            side="left", padx=6
        )
        self.result_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.result_var, wraplength=_CANVAS_W, justify="left").grid(
            row=5, column=0, sticky="w", pady=(4, 0)
        )

        button_row = ttk.Frame(outer)
        button_row.grid(row=6, column=0, sticky="we", pady=(12, 0))
        ttk.Button(button_row, text="Clear Region", command=self._clear_region).pack(side="left")
        ttk.Button(button_row, text="Cancel", command=self._on_cancel).pack(side="right")
        ttk.Button(button_row, text="Save Region", command=self._on_save).pack(side="right", padx=(0, 8))

        self._update_nav_state()

    # ----- file / page handling -------------------------------------------------

    def _browse_sample(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Choose a sample PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
        )
        if path:
            self._open_sample(Path(path))

    def _on_sample_selected(self, _event=None) -> None:
        idx = self.sample_combo.current()
        if idx >= 0:
            self._open_sample(self._sample_files[idx])

    def _open_sample(self, path: Path) -> None:
        try:
            doc = fitz.open(str(path))
            if doc.needs_pass or doc.page_count == 0:
                raise ValueError("encrypted or empty PDF")
        except Exception as exc:
            messagebox.showerror("Cannot open PDF", f"{path.name}:\n{exc}", parent=self)
            return
        if self._doc is not None:
            self._doc.close()
        self._doc = doc
        self._page_index = 0
        self.sample_var.set(path.name)
        self.result_var.set("")
        self._render_page()

    def _step_page(self, delta: int) -> None:
        if self._doc is None:
            return
        new_index = self._page_index + delta
        if 0 <= new_index < self._doc.page_count:
            self._page_index = new_index
            self.result_var.set("")
            self._render_page()

    def _update_nav_state(self) -> None:
        has_doc = self._doc is not None
        self.prev_btn.configure(state="normal" if has_doc and self._page_index > 0 else "disabled")
        self.next_btn.configure(
            state="normal" if has_doc and self._page_index < self._doc.page_count - 1 else "disabled"
        )
        if has_doc:
            self.page_var.set(f"Page {self._page_index + 1} of {self._doc.page_count}")

    def _current_page(self) -> fitz.Page | None:
        return self._doc[self._page_index] if self._doc is not None else None

    def _render_page(self) -> None:
        page = self._current_page()
        if page is None:
            return
        zoom = min(_CANVAS_W / page.rect.width, _CANVAS_H / page.rect.height)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        self._photo = tk.PhotoImage(data=base64.b64encode(pix.tobytes("png")))
        self._img_w, self._img_h = pix.width, pix.height
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw", tags="page")
        self._draw_region()
        self._update_nav_state()

    # ----- region drawing -------------------------------------------------------

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        return min(max(x, 0), self._img_w), min(max(y, 0), self._img_h)

    def _on_press(self, event) -> None:
        if self._doc is None:
            return
        self._drag_start = self._clamp(event.x, event.y)
        self.canvas.delete("drag")

    def _on_drag(self, event) -> None:
        if self._drag_start is None:
            return
        x, y = self._clamp(event.x, event.y)
        self.canvas.delete("drag")
        self.canvas.create_rectangle(
            *self._drag_start, x, y, outline=_REGION_COLOR, width=2, dash=(4, 2), tags="drag"
        )

    def _on_release(self, event) -> None:
        if self._drag_start is None:
            return
        x0, y0 = self._drag_start
        x1, y1 = self._clamp(event.x, event.y)
        self._drag_start = None
        self.canvas.delete("drag")
        if abs(x1 - x0) < _MIN_DRAG_PX or abs(y1 - y0) < _MIN_DRAG_PX:
            return
        self.region = [
            round(min(x0, x1) / self._img_w, 4),
            round(min(y0, y1) / self._img_h, 4),
            round(max(x0, x1) / self._img_w, 4),
            round(max(y0, y1) / self._img_h, 4),
        ]
        self.result_var.set("")
        self._draw_region()

    def _draw_region(self) -> None:
        self.canvas.delete("region")
        self.canvas.delete("match")
        self.region_var.set(format_region(self.region))
        if not self.region or not self._img_w:
            return
        x0, y0, x1, y1 = self.region
        self.canvas.create_rectangle(
            x0 * self._img_w, y0 * self._img_h, x1 * self._img_w, y1 * self._img_h,
            outline=_REGION_COLOR, width=2, stipple="gray12", fill=_REGION_COLOR, tags="region",
        )

    def _clear_region(self) -> None:
        self.region = None
        self.result_var.set("")
        self._draw_region()

    # ----- OCR preview ----------------------------------------------------------

    def _test_ocr(self) -> None:
        page = self._current_page()
        if page is None:
            self.result_var.set("Load a sample PDF first.")
            return
        if not self.region:
            self.result_var.set("Draw a region on the page first.")
            return
        pattern_str = self.pattern_var.get().strip()
        if not pattern_str:
            self.result_var.set("Enter a regex pattern in the main window first.")
            return
        try:
            pattern = re.compile(pattern_str)
        except re.error as exc:
            messagebox.showerror("Invalid Pattern", f"This regex pattern is invalid:\n{exc}", parent=self)
            return

        self.result_var.set("Running OCR...")
        self.config(cursor="watch")
        self.update_idletasks()
        try:
            scan = scan_page(page, pattern, self.region)
        except OcrUnavailableError as exc:
            self.result_var.set(f"❌ {exc}")
            return
        except Exception as exc:
            self.result_var.set(f"❌ OCR failed: {exc}")
            return
        finally:
            self.config(cursor="")

        self._draw_region()
        scale_x = self._img_w / page.rect.width
        scale_y = self._img_h / page.rect.height
        for match in scan.matches:
            r = match.rect
            self.canvas.create_rectangle(
                (r.x0 - page.rect.x0) * scale_x, (r.y0 - page.rect.y0) * scale_y,
                (r.x1 - page.rect.x0) * scale_x, (r.y1 - page.rect.y0) * scale_y,
                outline=_MATCH_COLOR, width=2, tags="match",
            )

        ocr_text = scan.ocr_text if len(scan.ocr_text) <= 160 else scan.ocr_text[:160] + "…"
        if scan.matches:
            found = ", ".join(repr(m.text) for m in scan.matches[:5])
            self.result_var.set(
                f"✅ {len(scan.matches)} match(es) would be removed (green): {found}\n"
                f"OCR read in region: {ocr_text or '(nothing)'}"
            )
        else:
            self.result_var.set(
                "⚠️ No match on this page. OCR read in region: "
                f"{ocr_text or '(nothing - is the region over the stamp?)'}"
            )

    # ----- close ----------------------------------------------------------------

    def _on_save(self) -> None:
        if not self.region:
            messagebox.showerror("No region", "Draw a region on the page first.", parent=self)
            return
        self.saved = True
        self._close()

    def _on_cancel(self) -> None:
        self.saved = False
        self._close()

    def _close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        self.destroy()
