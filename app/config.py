"""Persistent config.json handling.

The config file lives next to the running program: next to the .exe when
frozen by PyInstaller, or in the project root when run from source.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "acrobat_exe_path": "",
    "last_input_folder": "",
    "last_output_folder": "",
    "last_output_mode": "prefix_suffix",
    "last_prefix_suffix_text": "_stripped",
    "last_language_setting": "English (UK)",
    "last_dpi_setting": "600 dpi",
    "last_output_style_setting": "Searchable Image",
    "last_strip_mode": "full",
    "last_regex_pattern": "",
    "include_subfolders": False,
    "auto_run_adobe_ocr": False,
    # Image-region OCR mode: search region as fractions of the page
    # [x0, y0, x1, y1] (0.0-1.0), or None when no region has been drawn.
    "image_region": None,
}

LANGUAGES = [
    "English (UK)",
    "English (US)",
    "French",
    "German",
    "Spanish",
    "Italian",
    "Dutch",
]

OUTPUT_STYLES = [
    "Searchable Image",
    "Searchable Image (Exact)",
    "ClearScan",
]

DPI_OPTIONS = ["300 dpi", "400 dpi", "600 dpi"]


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def get_config_path() -> Path:
    return get_app_dir() / "config.json"


class Config:
    def __init__(self) -> None:
        self.path = get_config_path()
        self.existed_on_disk = self.path.exists()
        self.data: dict[str, Any] = dict(DEFAULTS)
        if self.existed_on_disk:
            self.load()

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for key in DEFAULTS:
                    if key in loaded:
                        self.data[key] = loaded[key]
        except (OSError, json.JSONDecodeError):
            self.data = dict(DEFAULTS)

    def save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        self.existed_on_disk = True

    def get(self, key: str) -> Any:
        return self.data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def update(self, **kwargs: Any) -> None:
        self.data.update(kwargs)
