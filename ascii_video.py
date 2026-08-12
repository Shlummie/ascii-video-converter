from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont


DEFAULT_CHARS = " .,:;irsXA253hMHGS#9B&@"

ProgressCallback = Callable[[dict[str, Any]], None]


@dataclass
class VideoConvertConfig:
    input: Path
    output: Path
    cols: int = 120
    font_size: int = 12
    font: Optional[str] = None
    fps: Optional[float] = None
    chars: str = DEFAULT_CHARS
    codec: str = "mp4v"
    no_audio: bool = False
    max_frames: Optional[int] = None
    progress_every: int = 25


@dataclass
class VideoConvertResult:
    output: Path
    frames_written: int
    source_width: int
    source_height: int
    source_fps: float
    output_fps: float
    ascii_cols: int
    ascii_rows: int
    output_width: int
    output_height: int
    audio_muxed: bool


def _emit(callback: Optional[ProgressCallback], kind: str, **kwargs: Any) -> None:
    if callback is None:
        return
    callback({"kind": kind, **kwargs})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert an MP4 into a colored ASCII art video."
    )
    parser.add_argument("input", type=Path, help="Input MP4 file path")
    parser.add_argument("output", type=Path, help="Output MP4 file path")
    parser.add_argument(
        "--cols",
        type=int,
        default=120,
        help="ASCII width in characters (default: 120)",
    )
    parser.add_argument(
        "--font-size",
        type=int,
        default=12,
        help="Font size used to render ASCII characters (default: 12)",
    )
    parser.add_argument(
        "--font",
        type=str,
        default=None,
        help="Optional path to a .ttf monospaced font",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        help="Override output FPS (default: source FPS)",
    )
    parser.add_argument(
        "--chars",
        type=str,
        default=DEFAULT_CHARS,
        help="Character ramp from dark to bright",
    )
    parser.add_argument(
        "--codec",
        type=str,
        default="mp4v",
        help="OpenCV fourcc codec (default: mp4v)",
    )
    parser.add_argument(
        "--no-audio",
        action="store_true",
        help="Do not attempt to copy/mux audio from the source",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Optional limit for testing",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=25,
        help="Print progress every N frames (default: 25)",
    )
    return parser.parse_args()


def args_to_config(args: argparse.Namespace) -> VideoConvertConfig:
    return VideoConvertConfig(
        input=args.input,
        output=args.output,
        cols=args.cols,
        font_size=args.font_size,
        font=args.font,
        fps=args.fps,
        chars=args.chars,
        codec=args.codec,
        no_audio=args.no_audio,
        max_frames=args.max_frames,
        progress_every=args.progress_every,
    )


def load_font(font_size: int, font_path: Optional[str]) -> ImageFont.ImageFont:
    if font_path:
        return ImageFont.truetype(font_path, font_size)

    candidates = [
        "DejaVuSansMono.ttf",
        "Consolas.ttf",
        "Courier New.ttf",
        "LiberationMono-Regular.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, font_size)
        except OSError:
            continue
    return ImageFont.load_default()


def font_cell_metrics(font: ImageFont.ImageFont) -> tuple[int, int, int, int]:
    bbox = font.getbbox("M")
    x0, y0, x1, y1 = bbox
    cell_w = max(1, x1 - x0)
    cell_h = max(1, y1 - y0)
    x_offset = -x0
    y_offset = -y0
    return cell_w, cell_h, x_offset, y_offset


def compute_rows(
    frame_w: int, frame_h: int, cols: int, cell_w: int, cell_h: int
) -> int:
    rows = int(round((frame_h / frame_w) * cols * (cell_w / cell_h)))
    return max(1, rows)


def build_glyph_masks(
    chars: str,
    font: ImageFont.ImageFont,
    cell_w: int,
    cell_h: int,
    x_offset: int,
    y_offset: int,
) -> np.ndarray:
    # Masks are rendered once and reused for every frame. This removes the slow
    # per-character draw loop from the hot path.
    masks = np.zeros((len(chars), cell_h, cell_w), dtype=np.uint8)
    for idx, ch in enumerate(chars):
        if ch == " ":
            continue
        glyph_img = Image.new("L", (cell_w, cell_h), color=0)
        draw = ImageDraw.Draw(glyph_img)
        draw.text((x_offset, y_offset), ch, font=font, fill=255)
        masks[idx] = np.asarray(glyph_img, dtype=np.uint8)
    return masks


class AsciiRenderer:
    def __init__(self, cols: int, font_size: int, font_path: Optional[str], chars: str):
        self.cols = cols
        self.chars = chars
        self.font = load_font(font_size, font_path)
        self.cell_w, self.cell_h, self.x_offset, self.y_offset = font_cell_metrics(self.font)
        self.glyph_masks = build_glyph_masks(
            chars=self.chars,
            font=self.font,
            cell_w=self.cell_w,
            cell_h=self.cell_h,
            x_offset=self.x_offset,
            y_offset=self.y_offset,
        )
        # 8-bit LUT for brightness -> char index.
        palette_size = max(0, len(self.chars) - 1)
        self.gray_to_char_index = (
            np.floor(np.arange(256, dtype=np.float32) * palette_size / 255.0)
            .astype(np.uint8)
            .reshape(256)
        )

    def rows_for_frame(self, frame_w: int, frame_h: int) -> int:
        return compute_rows(frame_w, frame_h, self.cols, self.cell_w, self.cell_h)

    def render_frame(self, frame_bgr: np.ndarray, rows: int) -> np.ndarray:
        sampled_bgr = cv2.resize(frame_bgr, (self.cols, rows), interpolation=cv2.INTER_AREA)
        sampled_gray = cv2.cvtColor(sampled_bgr, cv2.COLOR_BGR2GRAY)
        char_indices = self.gray_to_char_index[sampled_gray]  # (rows, cols)

        # Vectorized render:
        # glyph_alpha: (rows, cols, cell_h, cell_w)
        # colors:      (rows, cols, 1, 1, 3)
        glyph_alpha = self.glyph_masks[char_indices]
        alpha = glyph_alpha[..., None].astype(np.uint16)
        colors = sampled_bgr[:, :, None, None, :].astype(np.uint16)

        cells = ((alpha * colors + 127) // 255).astype(np.uint8)
        # Reassemble cells into a single image: (rows*cell_h, cols*cell_w, 3)
        frame_out = (
            cells.transpose(0, 2, 1, 3, 4)
            .reshape(rows * self.cell_h, self.cols * self.cell_w, 3)
        )
        return np.ascontiguousarray(frame_out)


def mux_audio_with_ffmpeg(
    source_video: Path,
    rendered_video: Path,
    output_video: Path,
    callback: Optional[ProgressCallback] = None,
) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        _emit(
            callback,
            "status",
            message="ffmpeg not found on PATH. Writing video without audio.",
        )
        return False

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(rendered_video),
        "-i",
        str(source_video),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-shortest",
        str(output_video),
    ]

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        _emit(callback, "status", message="ffmpeg mux failed. Writing video without audio.")
        if proc.stderr:
            _emit(callback, "status", message=proc.stderr.strip())
        return False
    return True


def validate_config(config: VideoConvertConfig) -> None:
    if not config.input.exists():
        raise FileNotFoundError(f"Input file not found: {config.input}")
    if config.input.suffix.lower() != ".mp4":
        # Not fatal. OpenCV can still decode many formats.
        pass
    if config.cols < 2:
        raise ValueError("--cols must be >= 2")
    if config.font_size < 4:
        raise ValueError("--font-size must be >= 4")
    if not config.chars:
        raise ValueError("--chars cannot be empty")
    if len(config.codec) != 4:
        raise ValueError("--codec must be a 4-character fourcc code (e.g. mp4v)")
    if config.max_frames is not None and config.max_frames <= 0:
        raise ValueError("--max-frames must be > 0")
    if config.progress_every <= 0:
        raise ValueError("--progress-every must be > 0")
    if config.fps is not None and config.fps <= 0:
        raise ValueError("--fps must be > 0")
    if config.font and not Path(config.font).exists():
        raise FileNotFoundError(f"Font file not found: {config.font}")


def convert_video(
    config: VideoConvertConfig,
    callback: Optional[ProgressCallback] = None,
) -> VideoConvertResult:
    validate_config(config)

    renderer = AsciiRenderer(
        cols=config.cols,
        font_size=config.font_size,
        font_path=config.font,
        chars=config.chars,
    )

    cap: Optional[cv2.VideoCapture] = None
    writer: Optional[cv2.VideoWriter] = None
    temp_video: Optional[Path] = None

    try:
        cap = cv2.VideoCapture(str(config.input))
        if not cap.isOpened():
            raise RuntimeError(f"Failed to open input video: {config.input}")

        src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        src_fps = float(cap.get(cv2.CAP_PROP_FPS)) or 0.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        if src_w <= 0 or src_h <= 0:
            raise RuntimeError("Could not read source dimensions.")

        output_fps = config.fps if config.fps else (src_fps if src_fps > 0 else 24.0)
        rows = renderer.rows_for_frame(src_w, src_h)
        out_w = renderer.cols * renderer.cell_w
        out_h = rows * renderer.cell_h

        effective_total = total_frames
        if config.max_frames is not None:
            effective_total = min(effective_total, config.max_frames) if effective_total else config.max_frames

        config.output.parent.mkdir(parents=True, exist_ok=True)

        fd, temp_video_str = tempfile.mkstemp(prefix="ascii_render_", suffix=".mp4")
        os.close(fd)
        temp_video = Path(temp_video_str)
        temp_video.unlink(missing_ok=True)

        fourcc = cv2.VideoWriter_fourcc(*config.codec)
        writer = cv2.VideoWriter(str(temp_video), fourcc, output_fps, (out_w, out_h))
        if not writer.isOpened():
            raise RuntimeError(
                f"Failed to open VideoWriter with codec '{config.codec}'. Try --codec mp4v."
            )

        _emit(
            callback,
            "status",
            message=f"Source: {src_w}x{src_h} @ {src_fps:.3f} fps, frames={total_frames or 'unknown'}",
        )
        _emit(
            callback,
            "status",
            message=(
                f"ASCII: {renderer.cols}x{rows} chars, rendered frame={out_w}x{out_h}px "
                f"@ {output_fps:.3f} fps"
            ),
        )
        _emit(
            callback,
            "status",
            message="Renderer: vectorized glyph atlas (fast path enabled)",
        )

        frame_idx = 0
        while True:
            if config.max_frames is not None and frame_idx >= config.max_frames:
                break

            ok, frame = cap.read()
            if not ok:
                break

            frame_idx += 1
            ascii_frame = renderer.render_frame(frame, rows=rows)
            writer.write(ascii_frame)

            should_report = frame_idx == 1 or frame_idx % config.progress_every == 0
            if effective_total and frame_idx == effective_total:
                should_report = True
            if should_report:
                percent = (frame_idx / effective_total) * 100.0 if effective_total else None
                _emit(
                    callback,
                    "progress",
                    frame=frame_idx,
                    total=effective_total or None,
                    percent=percent,
                )

        if frame_idx == 0:
            raise RuntimeError("No frames were decoded from the source video.")

        if writer is not None:
            writer.release()
            writer = None
        if cap is not None:
            cap.release()
            cap = None

        audio_muxed = False
        if config.no_audio:
            shutil.move(str(temp_video), str(config.output))
            _emit(callback, "status", message=f"Wrote video (no audio): {config.output}")
        else:
            audio_muxed = mux_audio_with_ffmpeg(
                source_video=config.input,
                rendered_video=temp_video,
                output_video=config.output,
                callback=callback,
            )
            if audio_muxed:
                temp_video.unlink(missing_ok=True)
                _emit(callback, "status", message=f"Wrote video with audio: {config.output}")
            else:
                shutil.move(str(temp_video), str(config.output))
                _emit(callback, "status", message=f"Wrote video without audio: {config.output}")

        result = VideoConvertResult(
            output=config.output,
            frames_written=frame_idx,
            source_width=src_w,
            source_height=src_h,
            source_fps=src_fps,
            output_fps=output_fps,
            ascii_cols=renderer.cols,
            ascii_rows=rows,
            output_width=out_w,
            output_height=out_h,
            audio_muxed=audio_muxed,
        )
        _emit(
            callback,
            "done",
            output=str(result.output),
            frames=result.frames_written,
            audio_muxed=result.audio_muxed,
        )
        return result

    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        if writer is not None:
            try:
                writer.release()
            except Exception:
                pass
        if temp_video is not None:
            try:
                temp_video.unlink(missing_ok=True)
            except Exception:
                pass


def _cli_callback(event: dict[str, Any]) -> None:
    kind = event.get("kind")
    if kind == "status":
        print(event.get("message", ""))
        return
    if kind == "progress":
        frame = event.get("frame")
        total = event.get("total")
        percent = event.get("percent")
        if total:
            print(f"Processed {frame}/{total} frames ({percent:.1f}%)")
        else:
            print(f"Processed {frame} frames")
        return


def main() -> int:
    args = parse_args()
    config = args_to_config(args)

    if config.input.suffix.lower() != ".mp4":
        print(
            "Warning: input is not .mp4; attempting conversion anyway.",
            file=sys.stderr,
        )

    try:
        convert_video(config, callback=_cli_callback)
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
