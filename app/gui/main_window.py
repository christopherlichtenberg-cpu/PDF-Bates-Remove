"""Main application window (Brief.md / UI Example_02.md)."""
from __future__ import annotations

import queue
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.config import Config
from app.gui.pattern_test_dialog import PatternTestDialog
from app.gui.region_dialog import RegionSelectDialog
from app.gui.settings_dialog import SettingsDialog
from app.image_bates import OcrUnavailableError, check_ocr_available
from app.logger import format_region
from app.paths import (
    OUTPUT_MODE_OVERWRITE,
    OUTPUT_MODE_PREFIX_SUFFIX,
    OUTPUT_MODE_SEPARATE,
    list_pdf_files,
    preview_filename,
)
from app.stripper import (
    STRIP_MODE_FULL,
    STRIP_MODE_IMAGE_REGION,
    STRIP_MODE_REGEX,
    InvalidRegexError,
    compile_pattern,
)
from app.worker import JobConfig, start_job

_LOG_ICON = {"OK": "✅", "WARN": "⚠️", "ERROR": "❌"}
_LOG_COLOR = {"OK": "#1a7f37", "WARN": "#9a6700", "ERROR": "#cf222e"}

_POLL_INTERVAL_MS = 100

_REGEX_HINT = "Only text matching this pattern will be removed from the OCR layer."
_IMAGE_REGION_HINT = (
    "Image mode: the selected region of every page is OCR'd and anything matching the pattern "
    "above is erased from the page image itself (plus any matching OCR text). Slower - intended "
    "as a second pass over files the options above couldn't fix."
)


class MainWindow(tk.Tk):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config_obj = config

        self.title("PDF OCR Layer Tool  v1.1")
        self.geometry("700x860")
        self.minsize(660, 560)

        self._job_thread = None
        self._event_queue = None
        self._cancel_token = None
        self._job_running = False

        self._build_vars()
        self._build_ui()
        self._refresh_dynamic_state()

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_vars(self) -> None:
        c = self.config_obj
        self.input_folder_var = tk.StringVar(value=c.get("last_input_folder"))
        self.include_subfolders_var = tk.BooleanVar(value=c.get("include_subfolders"))

        self.output_mode_var = tk.StringVar(value=c.get("last_output_mode"))
        self.output_folder_var = tk.StringVar(value=c.get("last_output_folder"))
        self.prefix_suffix_var = tk.StringVar(value=c.get("last_prefix_suffix_text"))
        self.preview_var = tk.StringVar(value="")

        self.strip_mode_var = tk.StringVar(value=c.get("last_strip_mode"))
        self.regex_pattern_var = tk.StringVar(value=c.get("last_regex_pattern"))
        self.image_region = c.get("image_region") or None
        self.region_summary_var = tk.StringVar(value=format_region(self.image_region))

        self.phase_var = tk.StringVar(value="Phase: Idle")
        self.counter_var = tk.StringVar(value="")
        self.current_file_var = tk.StringVar(value="")

        for var in (self.output_mode_var, self.prefix_suffix_var):
            var.trace_add("write", lambda *_: self._update_preview())
        for var in (self.output_mode_var, self.strip_mode_var):
            var.trace_add("write", lambda *_: self._refresh_dynamic_state())

    def _build_ui(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)

        scroll = _ScrollableFrame(container)
        scroll.pack(side="top", fill="both", expand=True)
        body = scroll.body

        self._build_input_section(body)
        self._build_output_section(body)
        self._build_stripping_section(body)
        self._build_progress_section(body)

        self._build_button_bar(container)

    def _build_input_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Input", padding=10)
        frame.pack(fill="x", padx=10, pady=(10, 6))
        frame.columnconfigure(0, weight=1)

        row = ttk.Frame(frame)
        row.grid(row=0, column=0, sticky="we")
        row.columnconfigure(0, weight=1)
        ttk.Label(row, text="Folder:").grid(row=0, column=0, sticky="w", columnspan=2)
        ttk.Entry(row, textvariable=self.input_folder_var).grid(row=1, column=0, sticky="we", padx=(0, 6))
        ttk.Button(row, text="Browse", command=self._browse_input_folder).grid(row=1, column=1)

        ttk.Checkbutton(
            frame, text="Include subfolders (mirrors structure in output)", variable=self.include_subfolders_var
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

    def _build_output_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Output", padding=10)
        frame.pack(fill="x", padx=10, pady=6)
        frame.columnconfigure(0, weight=1)

        ttk.Radiobutton(
            frame, text="Separate output folder", variable=self.output_mode_var, value=OUTPUT_MODE_SEPARATE
        ).grid(row=0, column=0, sticky="w")

        sep_row = ttk.Frame(frame)
        sep_row.grid(row=1, column=0, sticky="we", padx=(24, 0), pady=(2, 10))
        sep_row.columnconfigure(0, weight=1)
        self.output_folder_entry = ttk.Entry(sep_row, textvariable=self.output_folder_var)
        self.output_folder_entry.grid(row=0, column=0, sticky="we", padx=(0, 6))
        self.output_folder_browse_btn = ttk.Button(sep_row, text="Browse", command=self._browse_output_folder)
        self.output_folder_browse_btn.grid(row=0, column=1)

        ttk.Label(frame, text="Same location as originals:").grid(row=2, column=0, sticky="w")

        ttk.Radiobutton(
            frame, text="Overwrite originals", variable=self.output_mode_var, value=OUTPUT_MODE_OVERWRITE
        ).grid(row=3, column=0, sticky="w", padx=(24, 0))
        self.overwrite_warning = ttk.Label(
            frame,
            text="⚠️ Original files will be replaced",
            foreground="#cf222e",
        )
        self.overwrite_warning.grid(row=4, column=0, sticky="w", padx=(46, 0))

        ttk.Radiobutton(
            frame, text="Add prefix/suffix", variable=self.output_mode_var, value=OUTPUT_MODE_PREFIX_SUFFIX
        ).grid(row=5, column=0, sticky="w", padx=(24, 0), pady=(8, 0))
        ps_row = ttk.Frame(frame)
        ps_row.grid(row=6, column=0, sticky="w", padx=(46, 0))
        self.prefix_suffix_entry = ttk.Entry(ps_row, textvariable=self.prefix_suffix_var, width=20)
        self.prefix_suffix_entry.grid(row=0, column=0)
        self.preview_label = ttk.Label(frame, textvariable=self.preview_var, foreground="#555555")
        self.preview_label.grid(row=7, column=0, sticky="w", padx=(46, 0), pady=(4, 0))

    def _build_stripping_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Stripping Options", padding=10)
        frame.pack(fill="x", padx=10, pady=6)
        frame.columnconfigure(0, weight=1)

        ttk.Radiobutton(
            frame,
            text="Strip entire OCR text layer (flattens pages to images: looks the same, fully non-searchable)",
            variable=self.strip_mode_var,
            value=STRIP_MODE_FULL,
        ).grid(row=0, column=0, sticky="w")

        ttk.Radiobutton(
            frame, text="Strip matching pattern only:", variable=self.strip_mode_var, value=STRIP_MODE_REGEX
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))

        regex_row = ttk.Frame(frame)
        regex_row.grid(row=2, column=0, sticky="we", padx=(24, 0))
        regex_row.columnconfigure(0, weight=1)
        self.regex_entry = ttk.Entry(regex_row, textvariable=self.regex_pattern_var)
        self.regex_entry.grid(row=0, column=0, sticky="we", padx=(0, 6))
        self.regex_placeholder = ttk.Label(frame, text="e.g.  [A-Z]+-\\d+", foreground="#888888")
        self.regex_placeholder.grid(row=3, column=0, sticky="w", padx=(24, 0))
        self.test_pattern_btn = ttk.Button(regex_row, text="Test Pattern", command=self._open_test_pattern_dialog)
        self.test_pattern_btn.grid(row=0, column=1)

        self.regex_warning = ttk.Label(
            frame,
            text=_REGEX_HINT,
            foreground="#9a6700",
            wraplength=560,
            justify="left",
        )
        self.regex_warning.grid(row=4, column=0, sticky="w", padx=(24, 0), pady=(4, 0))

        ttk.Radiobutton(
            frame,
            text="Remove pattern from page images (OCR within a selected region) - uses the pattern above",
            variable=self.strip_mode_var,
            value=STRIP_MODE_IMAGE_REGION,
        ).grid(row=5, column=0, sticky="w", pady=(10, 0))

        region_row = ttk.Frame(frame)
        region_row.grid(row=6, column=0, sticky="we", padx=(24, 0), pady=(2, 0))
        self.select_region_btn = ttk.Button(region_row, text="Select Region...", command=self._open_region_dialog)
        self.select_region_btn.pack(side="left")
        self.region_summary_label = ttk.Label(region_row, textvariable=self.region_summary_var, foreground="#555555")
        self.region_summary_label.pack(side="left", padx=(8, 0))

    def _build_progress_section(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Progress", padding=10)
        frame.pack(fill="both", expand=True, padx=10, pady=6)
        frame.columnconfigure(0, weight=1)

        self.progress_bar = ttk.Progressbar(frame, orient="horizontal", mode="determinate")
        self.progress_bar.grid(row=0, column=0, sticky="we")

        status_row = ttk.Frame(frame)
        status_row.grid(row=1, column=0, sticky="we", pady=(6, 0))
        ttk.Label(status_row, textvariable=self.counter_var).pack(side="left")
        ttk.Label(status_row, textvariable=self.phase_var).pack(side="right")

        ttk.Label(frame, textvariable=self.current_file_var, foreground="#555555").grid(
            row=2, column=0, sticky="w", pady=(2, 6)
        )

        log_frame = ttk.Frame(frame)
        log_frame.grid(row=3, column=0, sticky="nsew")
        frame.rowconfigure(3, weight=1)
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=10, wrap="none", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=log_scroll.set)
        for key, color in _LOG_COLOR.items():
            self.log_text.tag_configure(key, foreground=color)

    def _build_button_bar(self, parent: ttk.Frame) -> None:
        bar = ttk.Frame(parent, padding=10)
        bar.pack(side="bottom", fill="x")

        self.run_btn = ttk.Button(bar, text="RUN", command=self._on_run)
        self.run_btn.pack(side="left")
        self.cancel_btn = ttk.Button(bar, text="CANCEL", command=self._on_cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="SETTINGS", command=self._open_settings_dialog).pack(side="right")

    def _refresh_dynamic_state(self) -> None:
        is_separate = self.output_mode_var.get() == OUTPUT_MODE_SEPARATE
        is_prefix_suffix = self.output_mode_var.get() == OUTPUT_MODE_PREFIX_SUFFIX
        is_overwrite = self.output_mode_var.get() == OUTPUT_MODE_OVERWRITE

        self.output_folder_entry.configure(state="normal" if is_separate else "disabled")
        self.output_folder_browse_btn.configure(state="normal" if is_separate else "disabled")
        self.prefix_suffix_entry.configure(state="normal" if is_prefix_suffix else "disabled")
        self.overwrite_warning.configure(foreground="#cf222e" if is_overwrite else "#c9a3a3")

        is_image_region = self.strip_mode_var.get() == STRIP_MODE_IMAGE_REGION
        uses_pattern = self.strip_mode_var.get() == STRIP_MODE_REGEX or is_image_region
        self.regex_entry.configure(state="normal" if uses_pattern else "disabled")
        self.test_pattern_btn.configure(state="normal" if uses_pattern else "disabled")
        self.select_region_btn.configure(state="normal" if is_image_region else "disabled")
        self.regex_warning.configure(text=_IMAGE_REGION_HINT if is_image_region else _REGEX_HINT)

        self._update_preview()

    def _update_preview(self) -> None:
        if self.output_mode_var.get() != OUTPUT_MODE_PREFIX_SUFFIX:
            self.preview_var.set("")
            return
        sample = preview_filename("document.pdf", self.prefix_suffix_var.get())
        self.preview_var.set(f"Preview: document.pdf → {sample}")

    def _browse_input_folder(self) -> None:
        path = filedialog.askdirectory(title="Select input folder")
        if path:
            self.input_folder_var.set(path)

    def _browse_output_folder(self) -> None:
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_folder_var.set(path)

    def _open_test_pattern_dialog(self) -> None:
        PatternTestDialog(self, self.regex_pattern_var)

    def _open_settings_dialog(self) -> None:
        SettingsDialog(self, self.config_obj)

    def _open_region_dialog(self) -> None:
        folder = self.input_folder_var.get().strip()
        sample_files = list_pdf_files(Path(folder), self.include_subfolders_var.get()) if folder else []
        dialog = RegionSelectDialog(self, sample_files[:500], self.regex_pattern_var, self.image_region)
        self.wait_window(dialog)
        if dialog.saved:
            self.image_region = dialog.region
            self.region_summary_var.set(format_region(self.image_region))
            self.config_obj.set("image_region", self.image_region)
            self.config_obj.save()

    def _validate_before_run(self) -> list[str]:
        errors = []
        input_folder = Path(self.input_folder_var.get()) if self.input_folder_var.get() else None
        if not input_folder or not input_folder.is_dir():
            errors.append("Please choose a valid input folder.")

        mode = self.output_mode_var.get()
        if mode == OUTPUT_MODE_SEPARATE:
            output_folder = self.output_folder_var.get().strip()
            if not output_folder:
                errors.append("Please choose an output folder.")
            elif input_folder and Path(output_folder).resolve() == input_folder.resolve():
                errors.append(
                    "Output folder is the same as the input folder — this would overwrite originals. "
                    "Choose 'Overwrite originals' explicitly, or pick a different folder."
                )

        if mode == OUTPUT_MODE_PREFIX_SUFFIX and not self.prefix_suffix_var.get().strip():
            errors.append("Please enter prefix/suffix text (or choose Overwrite).")

        strip_mode = self.strip_mode_var.get()
        if strip_mode in (STRIP_MODE_REGEX, STRIP_MODE_IMAGE_REGION):
            pattern = self.regex_pattern_var.get().strip()
            if not pattern:
                errors.append("Please enter a regex pattern, or choose Full strip.")
            else:
                try:
                    compile_pattern(pattern)
                except InvalidRegexError as exc:
                    errors.append(f"Regex pattern is invalid: {exc}")

        if strip_mode == STRIP_MODE_IMAGE_REGION:
            if not self.image_region:
                errors.append("Please select the region to search (Select Region...).")
            try:
                check_ocr_available()
            except OcrUnavailableError as exc:
                errors.append(str(exc))

        return errors

    def _on_run(self) -> None:
        errors = self._validate_before_run()
        if errors:
            messagebox.showerror("Cannot start", "\n".join(errors))
            return

        if self.output_mode_var.get() == OUTPUT_MODE_OVERWRITE:
            if not messagebox.askyesno(
                "Confirm overwrite",
                "Original files will be replaced and cannot be recovered. Continue?",
                icon="warning",
            ):
                return

        self._save_settings_to_config()
        self._clear_log()
        self.progress_bar.configure(value=0, maximum=1)
        self.counter_var.set("Starting...")
        self.current_file_var.set("")
        self.phase_var.set("Phase: Stripping")

        job = JobConfig(
            input_folder=Path(self.input_folder_var.get()),
            include_subfolders=self.include_subfolders_var.get(),
            output_mode=self.output_mode_var.get(),
            output_folder=self.output_folder_var.get(),
            prefix_suffix_text=self.prefix_suffix_var.get(),
            strip_mode=self.strip_mode_var.get(),
            regex_pattern=self.regex_pattern_var.get(),
            image_region=self.image_region,
        )
        self._job_thread, self._event_queue, self._cancel_token = start_job(job)
        self._job_running = True
        self._set_controls_enabled(False)
        self.after(_POLL_INTERVAL_MS, self._poll_queue)

    def _on_cancel(self) -> None:
        if self._cancel_token:
            self._cancel_token.cancel()
        self.cancel_btn.configure(state="disabled")
        self._append_log("WARN", "", "Cancelling... will stop after the current file")

    def _set_controls_enabled(self, enabled: bool) -> None:
        self.run_btn.configure(state="normal" if enabled else "disabled")
        self.cancel_btn.configure(state="disabled" if enabled else "normal")

    def _poll_queue(self) -> None:
        if self._event_queue is None:
            return
        try:
            while True:
                event = self._event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        if self._job_running:
            self.after(_POLL_INTERVAL_MS, self._poll_queue)

    def _handle_event(self, event: dict) -> None:
        etype = event["type"]
        if etype == "total":
            self.progress_bar.configure(maximum=max(event["total"], 1))
        elif etype == "phase":
            self.phase_var.set(f"Phase: {event['phase']}")
        elif etype == "progress":
            self.progress_bar.configure(value=event["current"])
            self.counter_var.set(f"Processing {event['current']} of {event['total']}")
            self.current_file_var.set(f"Current: {event['filename']}")
        elif etype == "log":
            self._append_log(event["icon"], event["filename"], event["message"])
        elif etype == "fatal":
            self._job_running = False
            self._set_controls_enabled(True)
            self.phase_var.set("Phase: Idle")
            messagebox.showerror("Cannot run", event["message"])
        elif etype == "done":
            self._job_running = False
            self._set_controls_enabled(True)
            self.phase_var.set("Phase: Idle")
            self._show_summary(event)

    def _show_summary(self, event: dict) -> None:
        lines = []
        if event.get("cancelled"):
            lines.append("Run cancelled by user.")
        lines.append(f"Total files found: {event['total']}")
        lines.append(f"Successfully stripped: {event['stripped']}")
        lines.append(f"Warnings: {event['warnings']}")
        lines.append(f"Errors: {event['errors']}")
        lines.append(f"\nLog saved to:\n{event['log_path']}")
        if event.get("error_log_path"):
            lines.append(f"\nError log saved to:\n{event['error_log_path']}")
        messagebox.showinfo("Run complete", "\n".join(lines))

    def _clear_log(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _append_log(self, icon_key: str, filename: str, message: str) -> None:
        icon = _LOG_ICON.get(icon_key, "")
        line = f"{icon}  {filename} - {message}" if filename else f"{icon}  {message}"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n", (icon_key,))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _save_settings_to_config(self) -> None:
        c = self.config_obj
        c.update(
            last_input_folder=self.input_folder_var.get(),
            include_subfolders=self.include_subfolders_var.get(),
            last_output_mode=self.output_mode_var.get(),
            last_output_folder=self.output_folder_var.get(),
            last_prefix_suffix_text=self.prefix_suffix_var.get(),
            last_strip_mode=self.strip_mode_var.get(),
            last_regex_pattern=self.regex_pattern_var.get(),
            image_region=self.image_region,
        )
        c.save()

    def _on_close(self) -> None:
        if self._job_running:
            if not messagebox.askyesno("Job running", "A job is still running. Quit anyway?"):
                return
            if self._cancel_token:
                self._cancel_token.cancel()
        self._save_settings_to_config()
        self.destroy()


class _ScrollableFrame(ttk.Frame):
    """A vertically scrollable container; use `.body` as the parent for content."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        vscroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.body = ttk.Frame(canvas)

        self.body.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
        canvas.configure(yscrollcommand=vscroll.set)

        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
