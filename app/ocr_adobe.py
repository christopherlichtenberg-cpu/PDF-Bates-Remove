"""Adobe Acrobat integration.

``verify_acrobat_path`` checks a user-picked path looks like a real Acrobat
install (used by First Run Setup and Settings).

NOTE: the v1.0 build also contained Stage 2 code that drove Acrobat XI's UI
via pywinauto (Recognize Text + Save As Optimized PDF). It was not wired into
the shipped build, and was not carried over when this source was
reconstructed from the v1.0 executable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AcrobatVerifyResult:
    ok: bool
    message: str


def verify_acrobat_path(path_str: str) -> AcrobatVerifyResult:
    if not path_str:
        return AcrobatVerifyResult(False, "No path provided")

    path = Path(path_str)
    if not path.is_file():
        return AcrobatVerifyResult(False, "File not found")
    if path.suffix.lower() != ".exe":
        return AcrobatVerifyResult(False, "Not an .exe file")
    if "acrobat" not in path.name.lower():
        return AcrobatVerifyResult(False, "Filename doesn't look like Acrobat.exe")

    description = _read_file_description(path)
    if description and "acrobat" in description.lower():
        return AcrobatVerifyResult(True, f"Acrobat found and verified ({description})")
    return AcrobatVerifyResult(True, "Acrobat.exe found (could not read version info to confirm)")


def _read_file_description(path: Path) -> str | None:
    try:
        import win32api

        translations = win32api.GetFileVersionInfo(str(path), "\\VarFileInfo\\Translation")
        lang, codepage = translations[0]
        info_path = f"\\StringFileInfo\\{lang:04x}{codepage:04x}\\FileDescription"
        return win32api.GetFileVersionInfo(str(path), info_path)
    except Exception:
        return None
