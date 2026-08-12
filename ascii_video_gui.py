from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path
from typing import Any, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except Exception as exc:  # pragma: no cover - startup-only fallback
    raise SystemExit(f"Tkinter is required for the GUI but is not available: {exc}")

import ascii_video


class AsciiVideoGui:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ASCII Video Converter (Colored)")
        self.root.geometry("880x640")
        self.root.minsize(760, 520)

        self.event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self._build_vars()
        self._build_ui()
        self.root.after(100, self._drain_events)

    def _build_vars(self) -> None:
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.font_var = tk.StringVar()

        self.cols_var = tk.StringVar(value="120")
        self.font_size_var = tk.StringVar(value="12")
        self.fps_var = tk.StringVar(value="")
        self.codec_var = tk.StringVar(value="mp4v")
        self.max_frames_var = tk.StringVar(value="")
        self.progress_every_var = tk.StringVar(value="25")
        self.no_audio_var = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Idle")

    def _build_ui(self) -> None:
        root_frame = ttk.Frame(self.root, padding=10)
        root_frame.pack(fill="both", expand=True)
        root_frame.columnconfigure(1, weight=1)
        root_frame.rowconfigure(6, weight=1)

        self._add_path_row(
            root_frame,
            row=0,
            label="Input MP4",
            var=self.input_var,
            browse_cmd=self._browse_input,
        )
        self._add_path_row(
            root_frame,
            row=1,
            label="Output MP4",
            var=self.output_var,
            browse_cmd=self._browse_output,
        )
        self._add_path_row(
            root_frame,
            row=2,
            label="Font (optional)",
            var=self.font_var,
            browse_cmd=self._browse_font,
        )

        options = ttk.LabelFrame(root_frame, text="Options", padding=10)
        options.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        for col in range(6):
            options.columnconfigure(col, weight=1 if col in (1, 3, 5) else 0)

        self._add_entry(options, 0, 0, "Cols", self.cols_var, width=10)
        self._add_entry(options, 0, 2, "Font Size", self.font_size_var, width=10)
        self._add_entry(options, 0, 4, "Codec", self.codec_var, width=10)

        self._add_entry(options, 1, 0, "FPS (blank=source)", self.fps_var, width=10)
        self._add_entry(options, 1, 2, "Max Frames (test)", self.max_frames_var, width=10)
        self._add_entry(
            options, 1, 4, "Progress Every", self.progress_every_var, width=10
        )

        ttk.Checkbutton(
            options,
            text="No audio (skip ffmpeg mux)",
            variable=self.no_audio_var,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        ttk.Label(
            options,
            text="Fast renderer is enabled by default (vectorized glyph atlas).",
        ).grid(row=2, column=3, columnspan=3, sticky="e", pady=(8, 0))

        controls = ttk.Frame(root_frame)
        controls.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        controls.columnconfigure(1, weight=1)

        self.start_btn = ttk.Button(controls, text="Start Convert", command=self._start)
        self.start_btn.grid(row=0, column=0, sticky="w")

        self.progress = ttk.Progressbar(
            controls, orient="horizontal", mode="determinate", maximum=100
        )
        self.progress.grid(row=0, column=1, sticky="ew", padx=(10, 10))

        self.progress_label = ttk.Label(controls, text="0%")
        self.progress_label.grid(row=0, column=2, sticky="e")

        status_frame = ttk.Frame(root_frame)
        status_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        status_frame.columnconfigure(1, weight=1)
        ttk.Label(status_frame, text="Status").grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.status_var).grid(
            row=0, column=1, sticky="w", padx=(8, 0)
        )

        log_frame = ttk.LabelFrame(root_frame, text="Log", padding=6)
        log_frame.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=14, wrap="word", state="disabled")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _add_path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        var: tk.StringVar,
        browse_cmd,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=4)
        btn = ttk.Button(parent, text="Browse", command=browse_cmd)
        btn.grid(row=row, column=2, sticky="e", pady=4)

    def _add_entry(
        self,
        parent: ttk.Frame,
        row: int,
        col: int,
        label: str,
        var: tk.StringVar,
        width: int = 12,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=col + 1, sticky="ew", padx=(6, 12), pady=2)

    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose input video",
            filetypes=[
                ("MP4 files", "*.mp4"),
                ("Video files", "*.mp4 *.mov *.avi *.mkv"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        self.input_var.set(path)
        if not self.output_var.get().strip():
            self.output_var.set(str(self._suggest_output(Path(path))))

    def _browse_output(self) -> None:
        initial = self.output_var.get().strip()
        if not initial and self.input_var.get().strip():
            initial = str(self._suggest_output(Path(self.input_var.get().strip())))
        path = filedialog.asksaveasfilename(
            title="Choose output video",
            defaultextension=".mp4",
            initialfile=Path(initial).name if initial else "output_ascii.mp4",
            initialdir=str(Path(initial).parent) if initial else None,
            filetypes=[("MP4 files", "*.mp4"), ("All files", "*.*")],
        )
        if path:
            self.output_var.set(path)

    def _browse_font(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose monospaced font (.ttf/.otf)",
            filetypes=[
                ("Font files", "*.ttf *.otf"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self.font_var.set(path)

    def _suggest_output(self, input_path: Path) -> Path:
        return input_path.with_name(f"{input_path.stem}_ascii.mp4")

    def _parse_optional_int(self, value: str) -> Optional[int]:
        text = value.strip()
        return int(text) if text else None

    def _parse_optional_float(self, value: str) -> Optional[float]:
        text = value.strip()
        return float(text) if text else None

    def _build_config(self) -> ascii_video.VideoConvertConfig:
        input_text = self.input_var.get().strip()
        if not input_text:
            raise ValueError("Please choose an input MP4 file.")
        input_path = Path(input_text)
        output_text = self.output_var.get().strip()
        if not output_text:
            output_text = str(self._suggest_output(input_path))
            self.output_var.set(output_text)
        output_path = Path(output_text)
        font_text = self.font_var.get().strip() or None

        config = ascii_video.VideoConvertConfig(
            input=input_path,
            output=output_path,
            cols=int(self.cols_var.get().strip()),
            font_size=int(self.font_size_var.get().strip()),
            font=font_text,
            fps=self._parse_optional_float(self.fps_var.get()),
            codec=self.codec_var.get().strip() or "mp4v",
            no_audio=bool(self.no_audio_var.get()),
            max_frames=self._parse_optional_int(self.max_frames_var.get()),
            progress_every=int(self.progress_every_var.get().strip()),
        )
        ascii_video.validate_config(config)
        return config

    def _set_running(self, running: bool) -> None:
        self.start_btn.configure(state="disabled" if running else "normal")
        self.root.configure(cursor="wait" if running else "")
        if running:
            self.status_var.set("Starting...")
        else:
            self.root.configure(cursor="")

    def _start(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return

        try:
            config = self._build_config()
        except Exception as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)
            return

        self._reset_progress()
        self._append_log(f"Queued: {config.input} -> {config.output}")
        self._set_running(True)

        self.worker_thread = threading.Thread(
            target=self._worker_run,
            args=(config,),
            daemon=True,
        )
        self.worker_thread.start()

    def _worker_run(self, config: ascii_video.VideoConvertConfig) -> None:
        try:
            ascii_video.convert_video(config, callback=self.event_queue.put)
            self.event_queue.put({"kind": "worker_finished", "success": True})
        except Exception as exc:
            self.event_queue.put(
                {
                    "kind": "worker_finished",
                    "success": False,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )

    def _drain_events(self) -> None:
        try:
            while True:
                event = self.event_queue.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")

        if kind == "status":
            message = str(event.get("message", ""))
            self.status_var.set(message)
            self._append_log(message)
            return

        if kind == "progress":
            frame = event.get("frame")
            total = event.get("total")
            percent = event.get("percent")
            if total and percent is not None:
                self.progress.configure(mode="determinate", maximum=100)
                self.progress["value"] = max(0.0, min(100.0, float(percent)))
                self.progress_label.configure(text=f"{float(percent):.1f}%")
                self.status_var.set(f"Processing frame {frame}/{total}")
            else:
                # Unknown length source. Move a determinate bar in a cycle.
                current = float(self.progress["value"])
                self.progress.configure(mode="determinate", maximum=100)
                self.progress["value"] = (current + 7) % 100
                self.progress_label.configure(text=f"{int(frame)} frames")
                self.status_var.set(f"Processing frame {frame}")
            return

        if kind == "done":
            frames = event.get("frames")
            output = event.get("output")
            audio_muxed = bool(event.get("audio_muxed"))
            self.progress["value"] = 100
            self.progress_label.configure(text="100%")
            self.status_var.set("Completed")
            self._append_log(
                f"Done: {frames} frames -> {output} ({'with audio' if audio_muxed else 'video only'})"
            )
            return

        if kind == "worker_finished":
            self._set_running(False)
            if event.get("success"):
                messagebox.showinfo("Done", "Conversion finished.", parent=self.root)
            else:
                msg = str(event.get("message", "Conversion failed"))
                self.status_var.set("Failed")
                self._append_log(msg)
                tb = event.get("traceback")
                if tb:
                    self._append_log(tb)
                messagebox.showerror("Conversion failed", msg, parent=self.root)
            return

        self._append_log(str(event))

    def _reset_progress(self) -> None:
        self.progress.configure(mode="determinate", maximum=100)
        self.progress["value"] = 0
        self.progress_label.configure(text="0%")

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main() -> int:
    root = tk.Tk()
    try:
        root.iconname("ASCII Video")
    except Exception:
        pass
    AsciiVideoGui(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
