"""Settings dialog: lets the user update the saved Acrobat path later."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk

from app.config import Config
from app.ocr_adobe import verify_acrobat_path


class SettingsDialog(tk.Toplevel):
    def __init__(self, master: tk.Misc, config: Config) -> None:
        super().__init__(master)
        self.config_obj = config

        self.title("Settings")
        self.resizable(False, False)
        self.transient(master)

        self._build_ui()

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
        outer = ttk.Frame(self, padding=16)
        outer.grid(row=0, column=0, sticky="nsew")

        ttk.Label(outer, text="Adobe Acrobat location:").grid(row=0, column=0, columnspan=3, sticky="w")

        self.path_var = tk.StringVar(value=self.config_obj.get("acrobat_exe_path"))
        ttk.Entry(outer, textvariable=self.path_var, width=52).grid(
            row=1, column=0, columnspan=2, sticky="we", pady=8
        )
        ttk.Button(outer, text="Browse...", command=self._browse).grid(row=1, column=2, padx=(8, 0))

        self.status_var = tk.StringVar(value="")
        ttk.Label(outer, textvariable=self.status_var, wraplength=440, justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )

        button_row = ttk.Frame(outer)
        button_row.grid(row=3, column=0, columnspan=3, sticky="e")
        ttk.Button(button_row, text="Save", command=self._save).pack(side="left", padx=(0, 8))
        ttk.Button(button_row, text="Cancel", command=self.destroy).pack(side="left")

        if self.path_var.get():
            self._verify(silent_ok=True)

    def _browse(self) -> None:
        path = filedialog.askopenfilename(
            title="Locate Acrobat.exe",
            filetypes=[("Acrobat.exe", "Acrobat.exe"), ("Executable files", "*.exe"), ("All files", "*.*")],
        )
        if path:
            self.path_var.set(path)
            self._verify()

    def _verify(self, silent_ok: bool = False) -> bool:
        result = verify_acrobat_path(self.path_var.get())
        icon = "✅" if result.ok else "❌"
        self.status_var.set(f"{icon} {result.message}")
        return result.ok

    def _save(self) -> None:
        self._verify()
        self.config_obj.set("acrobat_exe_path", self.path_var.get())
        self.config_obj.save()
        self.destroy()
