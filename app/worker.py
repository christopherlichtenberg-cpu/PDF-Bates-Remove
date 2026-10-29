"""Background-thread processing pipeline.

Runs on a worker thread so the Tkinter GUI stays responsive; communicates
progress back to the GUI thread exclusively through a thread-safe
queue.Queue, drained by the GUI via root.after(...) polling. Never touches
Tkinter widgets directly.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path

from app.logger import RunLogger, RunSettings
from app.paths import OutputPlan, list_pdf_files
from app.stripper import (
    STATUS_NO_MATCHES,
    STATUS_NO_OCR_LAYER,
    STATUS_PARTIAL,
    STATUS_SKIPPED_CORRUPT,
    STATUS_SKIPPED_ENCRYPTED,
    STATUS_SKIPPED_LOCKED,
    STATUS_STRIPPED,
    STRIP_MODE_IMAGE_REGION,
    STRIP_MODE_REGEX,
    InvalidRegexError,
    StripResult,
    compile_pattern,
    strip_pdf,
)

PHASE_STRIPPING = "Stripping"

_SKIP_MESSAGES = {
    STATUS_SKIPPED_ENCRYPTED: "encrypted, skipped",
    STATUS_SKIPPED_CORRUPT: "corrupt/unreadable, skipped",
    STATUS_SKIPPED_LOCKED: "file locked or open elsewhere, skipped",
}


@dataclass
class JobConfig:
    input_folder: Path
    include_subfolders: bool
    output_mode: str
    output_folder: str
    prefix_suffix_text: str
    strip_mode: str
    regex_pattern: str
    image_region: list[float] | None = None


class CancelToken:
    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


def start_job(job: JobConfig) -> tuple[threading.Thread, queue.Queue, CancelToken]:
    event_queue: queue.Queue = queue.Queue()
    cancel_token = CancelToken()
    thread = threading.Thread(target=_run_job, args=(job, event_queue, cancel_token), daemon=True)
    thread.start()
    return thread, event_queue, cancel_token


def _run_job(job: JobConfig, event_queue: queue.Queue, cancel_token: CancelToken) -> None:
    try:
        _run_job_inner(job, event_queue, cancel_token)
    except Exception as exc:
        event_queue.put({"type": "fatal", "message": f"Unexpected error: {exc}"})


def _run_job_inner(job: JobConfig, event_queue: queue.Queue, cancel_token: CancelToken) -> None:
    if job.strip_mode in (STRIP_MODE_REGEX, STRIP_MODE_IMAGE_REGION):
        # Fail fast on a bad pattern before touching any file.
        try:
            compile_pattern(job.regex_pattern)
        except InvalidRegexError as exc:
            event_queue.put({"type": "fatal", "message": f"Invalid regex pattern: {exc}"})
            return

    files = list_pdf_files(job.input_folder, job.include_subfolders)
    total = len(files)
    if total == 0:
        event_queue.put({"type": "fatal", "message": "No PDF files found in the selected input folder."})
        return

    output_plan = OutputPlan(job.output_mode, job.output_folder, job.prefix_suffix_text)
    if output_plan.would_silently_overwrite():
        event_queue.put(
            {
                "type": "fatal",
                "message": "Prefix/Suffix text is empty - this would silently overwrite originals. "
                "Enter prefix/suffix text, or choose Overwrite explicitly.",
            }
        )
        return

    log_dir = _resolve_log_dir(job)
    settings = RunSettings(
        input_folder=str(job.input_folder),
        output_folder=_display_output_location(job, output_plan),
        include_subfolders=job.include_subfolders,
        strip_mode=job.strip_mode,
        regex_pattern=job.regex_pattern,
        adobe_ocr_enabled=False,
        language="",
        dpi="",
        output_style="",
        image_region=job.image_region,
    )
    logger = RunLogger(log_dir, settings)
    logger.set_total_found(total)

    event_queue.put({"type": "total", "total": total})
    event_queue.put({"type": "phase", "phase": PHASE_STRIPPING})

    for idx, file_path in enumerate(files, start=1):
        if cancel_token.is_cancelled():
            logger.log_cancelled()
            break

        event_queue.put({"type": "progress", "current": idx, "total": total, "filename": file_path.name})

        output_path = output_plan.resolve(job.input_folder, file_path)
        result = strip_pdf(
            file_path,
            output_path,
            job.strip_mode,
            job.regex_pattern,
            image_region=job.image_region,
        )
        _record_strip_result(logger, event_queue, file_path, result)

    logger.write()
    event_queue.put(
        {
            "type": "done",
            "log_path": str(logger.log_path),
            "error_log_path": str(logger.error_log_path) if logger.errors else None,
            "total": logger.total_found,
            "stripped": logger.stripped_count,
            "warnings": logger.warning_count,
            "errors": logger.error_count,
            "cancelled": cancel_token.is_cancelled(),
        }
    )


def _record_strip_result(
    logger: RunLogger, event_queue: queue.Queue, file_path: Path, result: StripResult
) -> None:
    name = file_path.name
    if result.status == STATUS_STRIPPED:
        logger.log_stripped(name, "stripped" if result.message == "stripped successfully" else result.message)
        event_queue.put({"type": "log", "icon": "OK", "filename": name, "message": result.message})
        return
    if result.status == STATUS_PARTIAL:
        logger.log_stripped_with_warning(name, result.message)
        event_queue.put({"type": "log", "icon": "WARN", "filename": name, "message": result.message})
        return
    if result.status == STATUS_NO_OCR_LAYER:
        logger.log_warning(name, "no OCR layer found")
        event_queue.put({"type": "log", "icon": "WARN", "filename": name, "message": "no OCR layer found"})
        return
    if result.status == STATUS_NO_MATCHES:
        message = "no pattern matches found"
        if result.message.endswith("selected region"):
            message = "no pattern matches found in the selected region"
        logger.log_warning(name, message)
        event_queue.put({"type": "log", "icon": "WARN", "filename": name, "message": message})
        return
    message = _SKIP_MESSAGES.get(result.status, result.message)
    logger.log_error(name, result.message, str(file_path))
    event_queue.put({"type": "log", "icon": "ERROR", "filename": name, "message": message})


def _resolve_log_dir(job: JobConfig) -> Path:
    if job.output_mode == "separate":
        return Path(job.output_folder)
    return job.input_folder


def _display_output_location(job: JobConfig, output_plan: OutputPlan) -> str:
    if job.output_mode == "separate":
        return job.output_folder
    if job.output_mode == "overwrite":
        return f"{job.input_folder} (overwrite originals)"
    return f"{job.input_folder} (prefix/suffix: {job.prefix_suffix_text})"
