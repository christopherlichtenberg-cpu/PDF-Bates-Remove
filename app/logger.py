"""Run log / error log writer, matching the exact format specified in Brief.md."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ICON_OK = "✅"
ICON_WARN = "⚠️"
ICON_ERROR = "❌"

SEPARATOR = "================================"


@dataclass
class LogEntry:
    time_str: str
    icon: str
    filename: str
    message: str


@dataclass
class ErrorEntry:
    filename: str
    message: str
    path: str


@dataclass
class RunSettings:
    input_folder: str
    output_folder: str
    include_subfolders: bool
    strip_mode: str
    regex_pattern: str
    adobe_ocr_enabled: bool
    language: str
    dpi: str
    output_style: str
    image_region: list[float] | None = None

    def strip_mode_display(self) -> str:
        if self.strip_mode == "regex":
            return f"Regex({self.regex_pattern})"
        if self.strip_mode == "image_region":
            return f"Image region OCR - Regex({self.regex_pattern}) in {format_region(self.image_region)}"
        return "Full"


def format_region(region: list[float] | None) -> str:
    if not region:
        return "no region"
    x0, y0, x1, y1 = (round(v * 100) for v in region)
    return f"region x {x0}-{x1}%, y {y0}-{y1}% of page"


class RunLogger:
    def __init__(self, log_dir: Path, settings: RunSettings) -> None:
        self.log_dir = log_dir
        self.settings = settings
        self.started_at = datetime.now()
        stamp = self.started_at.strftime("%Y%m%d_%H%M%S")
        self.log_path = log_dir / f"strip_ocr_log_{stamp}.txt"
        self.error_log_path = log_dir / f"strip_ocr_ERRORS_{stamp}.txt"

        self.entries: list[LogEntry] = []
        self.errors: list[ErrorEntry] = []

        self.total_found = 0
        self.stripped_count = 0
        self.warning_count = 0
        self.error_count = 0
        self.ocr_completed_count = 0
        self.ocr_optimize_failed_count = 0

    def set_total_found(self, count: int) -> None:
        self.total_found = count

    def log_ok(self, filename: str, message: str) -> None:
        self.entries.append(LogEntry(_now(), ICON_OK, filename, message))

    def log_warning(self, filename: str, message: str) -> None:
        self.entries.append(LogEntry(_now(), ICON_WARN, filename, message))
        self.warning_count += 1

    def log_error(self, filename: str, message: str, path: str) -> None:
        self.entries.append(LogEntry(_now(), ICON_ERROR, filename, message))
        self.error_count += 1
        self.errors.append(ErrorEntry(filename, message, path))

    def log_stripped(self, filename: str, message: str = "stripped") -> None:
        self.log_ok(filename, message)
        self.stripped_count += 1

    def log_stripped_with_warning(self, filename: str, message: str) -> None:
        """File was written, but something needs a human to check it
        (e.g. some pages had no match in the image region)."""
        self.log_warning(filename, message)
        self.stripped_count += 1

    def log_ocr_result(
        self, filename: str, ok: bool, message: str, timed_out: bool, path: str
    ) -> None:
        if ok:
            self.log_ok(filename, message)
            self.ocr_completed_count += 1
        elif timed_out:
            self.log_warning(filename, message)
            self.ocr_optimize_failed_count += 1
        else:
            self.log_error(filename, message, path)
            self.ocr_optimize_failed_count += 1

    def log_cancelled(self) -> None:
        self.entries.append(LogEntry(_now(), ICON_WARN, "", "Cancelled by user"))

    def write(self) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._write_main_log()
        if self.errors:
            self._write_error_log()

    def _write_main_log(self) -> None:
        s = self.settings
        lines = [
            SEPARATOR,
            "PDF OCR Strip Tool - Run Log",
            f"Date: {self.started_at:%Y-%m-%d %H:%M:%S}",
            SEPARATOR,
            "Settings:",
            f"- Input folder: {s.input_folder}",
            f"- Output folder: {s.output_folder}",
            f"- Include subfolders: {'Yes' if s.include_subfolders else 'No'}",
            f"- Strip mode: {s.strip_mode_display()}",
            f"- Adobe OCR: {'Yes' if s.adobe_ocr_enabled else 'No'}",
        ]
        if s.adobe_ocr_enabled:
            lines += [
                f"- Language: {s.language}",
                f"- DPI: {s.dpi}",
                f"- Output style: {s.output_style}",
            ]
        lines += [SEPARATOR, "Processing:"]
        for e in self.entries:
            if e.filename:
                lines.append(f"{e.time_str} | {e.icon} | {e.filename} | {e.message}")
            else:
                lines.append(f"{e.time_str} | {e.icon} | {e.message}")
        lines += [
            SEPARATOR,
            "Summary:",
            f"Total files found:    {self.total_found}",
            f"Successfully stripped: {self.stripped_count}",
            f"Warnings:              {self.warning_count}",
            f"Errors:                {self.error_count}",
        ]
        if s.adobe_ocr_enabled:
            lines.append(f"OCR/Optimize completed: {self.ocr_completed_count}")
            lines.append(f"OCR/Optimize failed:    {self.ocr_optimize_failed_count}")
        lines.append(SEPARATOR)

        self.log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _write_error_log(self) -> None:
        lines = [
            SEPARATOR,
            "PDF OCR Strip Tool - Error Log",
            f"Date: {self.started_at:%Y-%m-%d %H:%M:%S}",
            SEPARATOR,
        ]
        for err in self.errors:
            lines.append(err.filename)
            lines.append(f"Error: {err.message}")
            lines.append(f"Path: {err.path}")
            lines.append(SEPARATOR)
        lines.append(f"Total errors: {len(self.errors)}")
        lines.append(SEPARATOR)

        self.error_log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")
