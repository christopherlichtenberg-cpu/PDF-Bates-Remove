"""Entry point for the PDF OCR Layer Tool.

When built with PyInstaller in --windowed mode there is no console: sys.stdout
/ sys.stderr are None, and anything that writes to them (including some
third-party libraries) would crash. Point them at the null device first.
"""

from __future__ import annotations

import sys

if sys.stdout is None:
    sys.stdout = open("nul" if sys.platform == "win32" else "/dev/null", "w")
if sys.stderr is None:
    sys.stderr = open("nul" if sys.platform == "win32" else "/dev/null", "w")

import tkinter as tk
import traceback
from tkinter import messagebox

from app.config import Config
from app.gui.first_run import FirstRunDialog
from app.gui.main_window import MainWindow


def _show_fatal_error(title: str, exc: BaseException) -> None:
    details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(title, f"{exc}\n\nDetails:\n{details[-2000:]}")
        root.destroy()
    except Exception:
        pass


def main() -> None:
    config = Config()

    if not config.existed_on_disk:
        temp_root = tk.Tk()
        temp_root.withdraw()
        dialog = FirstRunDialog(temp_root, config)
        temp_root.wait_window(dialog)
        temp_root.destroy()
        if not dialog.completed:
            return

    app = MainWindow(config)
    app.report_callback_exception = _tk_callback_exception_handler
    app.mainloop()


def _tk_callback_exception_handler(exc_type, exc_value, exc_tb) -> None:
    details = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    messagebox.showerror("Unexpected error", f"{exc_value}\n\nDetails:\n{details[-2000:]}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        _show_fatal_error("PDF OCR Layer Tool failed to start", exc)
