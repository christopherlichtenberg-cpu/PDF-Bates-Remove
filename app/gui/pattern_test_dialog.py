"""'Test Pattern' mini dialog: preview what a regex would match/remove."""
from __future__ import annotations

import re
import tkinter as tk
from tkinter import messagebox, ttk


class PatternTestDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, pattern_var: tk.StringVar) -> None:
        super().__init__(master)
        self.pattern_var = pattern_var

        self.title("Test Pattern")
        self.resizable(False, False)
        self.transient(master)

        self._build_ui()

        self.grab_set()
        self.lift()
        self.focus_force()
        self.update_idletasks()
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=16)
        outer.grid(row=0, column=0, sticky="nsew")

        pattern_row = ttk.Frame(outer)
        pattern_row.grid(row=0, column=0, sticky="w")
        ttk.Label(pattern_row, text="Pattern: ").pack(side="left")
        self.pattern_display_var = tk.StringVar()
        ttk.Label(pattern_row, textvariable=self.pattern_display_var, font=("Consolas", 10, "bold")).pack(
            side="left"
        )

        ttk.Label(outer, text="Paste sample OCR text below:").grid(
            row=1, column=0, sticky="w", pady=(12, 4)
        )
        self.sample_text = tk.Text(outer, width=64, height=8, wrap="word")
        self.sample_text.grid(row=2, column=0, sticky="nsew")
        self.sample_text.tag_configure("match", background="#ffc9c9", foreground="#8a0000")

        ttk.Button(outer, text="Test Pattern", command=self._run_test).grid(row=3, column=0, sticky="w", pady=8)

        ttk.Label(outer, text="Result (matches highlighted above would be removed):").grid(
            row=4, column=0, sticky="w"
        )
        self.result_var = tk.StringVar(value="(no test run yet)")
        ttk.Label(outer, textvariable=self.result_var, wraplength=460, justify="left").grid(
            row=5, column=0, sticky="w"
        )

        ttk.Button(outer, text="Close", command=self.destroy).grid(row=6, column=0, sticky="e", pady=(14, 0))

    def _run_test(self) -> None:
        pattern_str = self.pattern_var.get()
        self.pattern_display_var.set(pattern_str or "(empty)")
        self.sample_text.tag_remove("match", "1.0", "end")

        if not pattern_str:
            self.result_var.set("Enter a pattern in the main window first.")
            return

        try:
            pattern = re.compile(pattern_str)
        except re.error as exc:
            messagebox.showerror("Invalid Pattern", f"This regex pattern is invalid:\n{exc}", parent=self)
            return

        text = self.sample_text.get("1.0", "end-1c")
        matches = [m for m in pattern.finditer(text) if m.start() != m.end()]
        if not matches:
            self.result_var.set("No matches found in the sample text.")
            return

        for m in matches:
            self.sample_text.tag_add("match", f"1.0+{m.start()}c", f"1.0+{m.end()}c")

        preview = ", ".join(repr(m.group()) for m in matches[:10])
        more = "" if len(matches) <= 10 else f" (+{len(matches) - 10} more)"
        self.result_var.set(f"{len(matches)} match(es) would be removed: {preview}{more}")
