"""Filename / folder resolution helpers shared by the GUI and the worker.

Kept free of Tkinter and PyMuPDF imports so it can be unit-tested in
isolation and reused unchanged once Adobe OCR (Stage 2) needs the same
output-path logic.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

OUTPUT_MODE_SEPARATE = "separate"
OUTPUT_MODE_OVERWRITE = "overwrite"
OUTPUT_MODE_PREFIX_SUFFIX = "prefix_suffix"

_SEPARATOR_CHARS = ("_", "-", " ")


def is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"


def list_pdf_files(input_folder: Path, include_subfolders: bool) -> list[Path]:
    if not input_folder.is_dir():
        return []
    if include_subfolders:
        found = [p for p in input_folder.rglob("*.pdf") if p.is_file()]
        found += [p for p in input_folder.rglob("*.PDF") if p.is_file()]
    else:
        found = [p for p in input_folder.glob("*.pdf") if p.is_file()]
        found += [p for p in input_folder.glob("*.PDF") if p.is_file()]
    unique = sorted(set(found), key=lambda p: str(p).lower())
    return unique


def apply_prefix_suffix(stem: str, text: str) -> str:
    """Text ending in a separator (e.g. "CONF_") is treated as a prefix;
    anything else (e.g. "_stripped") is appended as a suffix."""
    if not text:
        return stem
    starts_sep = text[0] in _SEPARATOR_CHARS
    ends_sep = text[-1] in _SEPARATOR_CHARS
    if ends_sep and not starts_sep:
        return f"{text}{stem}"
    return f"{stem}{text}"


def preview_filename(original_name: str, text: str) -> str:
    p = Path(original_name)
    new_stem = apply_prefix_suffix(p.stem, text)
    return f"{new_stem}{p.suffix}"


@dataclass
class OutputPlan:
    output_mode: str
    output_folder: str = ""
    prefix_suffix_text: str = ""

    def resolve(self, input_root: Path, file_path: Path) -> Path:
        rel = file_path.relative_to(input_root)
        if self.output_mode == OUTPUT_MODE_SEPARATE:
            return Path(self.output_folder) / rel
        if self.output_mode == OUTPUT_MODE_OVERWRITE:
            return file_path
        if self.output_mode == OUTPUT_MODE_PREFIX_SUFFIX:
            new_name = preview_filename(rel.name, self.prefix_suffix_text)
            return file_path.with_name(new_name)
        raise ValueError(f"Unknown output mode: {self.output_mode}")

    def would_silently_overwrite(self) -> bool:
        return self.output_mode == OUTPUT_MODE_PREFIX_SUFFIX and not self.prefix_suffix_text.strip()


def ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def get_resource_dir() -> Path:
    """Folder holding bundled read-only resources (e.g. ``tessdata``):
    PyInstaller's unpack dir when frozen, the project root from source."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent.parent


def get_tessdata_dir() -> Path:
    return get_resource_dir() / "tessdata"
