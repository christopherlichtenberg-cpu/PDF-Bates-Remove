"""First Run Setup screen (Brief.md / UI Example_01.md)."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from app.config import Config
from app.ocr_adobe import verify_acrobat_path


class FirstRunDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, config: Config) -> None:
        super().__init__(master)
        self.config_obj = config
        self.completed = False

        self.title("PDF OCR Layer Tool - First Run Setup")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._build_ui()

        # The master root is withdrawn, so this dialog must explicitly show
        # itself and grab focus or it can open behind other windows.
        self.deiconify()
        self.grab_set()
        self.lift()
        self.focus_force()
        self.update_idletasks()
        self._center_on_screen()

    def _center_on_screen(self) -> None:
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

    def _build_ui(self) -> None:
        pad = {"padx": 16, "pady": 6}
        outer = ttk.Frame(self, padding=12)
        outer.grid(row=0, column=0, sticky="nsew")

        ttk.Label(outer, text="Welcome! Let's get set up.", font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )
        ttk.Label(outer, text="Please locate your Adobe Acrobat executable:").grid(
            row=1, column=0, columnspan=3, sticky="w"
        )

        self.path_var = tk.StringVar(value=self.config_obj.get("acrobat_exe_path"))
        entry = ttk.Entry(outer, textvariable=self.path_var, width=52)
        entry.grid(row=2, column=0, columnspan=2, sticky="we", pady=8)
        ttk.Button(outer, text="Browse...", command=self._browse).grid(row=2, column=2, padx=(8, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status_var, wraplength=440, justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )

        button_row = ttk.Frame(outer)
        button_row.grid(row=4, column=0, columnspan=3, sticky="we")
        self.continue_btn = ttk.Button(
            button_row, text="Continue to Tool →", command=self._continue, state="disabled"
        )
        self.continue_btn.pack(side="left")
        ttk.Button(button_row, text="Skip for now", command=self._skip).pack(side="left", padx=(8, 0))

        ttk.Label(
            outer,
            text="(You can change this later in Settings)",
            foreground="#666666",
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(12, 0))

        if self.path_var.get():
            self._verify()

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Locate Acrobat.exe",
            filetypes=[("Acrobat.exe", "Acrobat.exe"), ("Executable files", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.path_var.set(path)
            self._verify()

    def _verify(self) -> None:
        result = verify_acrobat_path(self.path_var.get())
        if result.ok:
            self.status_var.set(f"✅ {result.message}")
            self.continue_btn.state(["!disabled"])
            return
        self.status_var.set(f"❌ {result.message}")
        self.continue_btn.state(["disabled"])

    def _continue(self) -> None:
        self.config_obj.set("acrobat_exe_path", self.path_var.get())
        self.config_obj.save()
        self.completed = True
        self.destroy()

    def _skip(self) -> None:
        self.config_obj.set("acrobat_exe_path", self.path_var.get())
        self.config_obj.save()
        self.completed = True
        self.destroy()

    def _on_close(self) -> None:
        self.completed = False
        self.destroy()
