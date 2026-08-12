# ASCII Video Converter

A Python command-line and desktop tool that renders ordinary video frames as ASCII art and writes the result to a new video. The GUI and CLI share the same conversion core.

## Features

- Reads video frames with OpenCV.
- Resamples each frame to a configurable character grid.
- Maps luminance to an ASCII ramp and optionally colors glyphs from the source frame.
- Displays progress with `tqdm` in command-line mode.
- Provides a Tkinter file picker and conversion controls for desktop use.
- Can call FFmpeg to copy the original audio track into the rendered output when FFmpeg is installed.

## Processing pipeline

```text
input video -> OpenCV decode -> resize/sample -> luminance glyph selection
            -> Pillow text render -> OpenCV encode -> optional FFmpeg audio mux
```

Conversion is offline. The program does not upload input frames or output media.

## Install

Requirements: Python 3.10+, a working video codec stack for OpenCV, and optional FFmpeg for audio passthrough.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

GUI:

```bash
python ascii_video_gui.py
```

or double-click `Run_ASCII_Video_GUI.bat`.

CLI:

```bash
python ascii_video.py input.mp4 output.mp4
python ascii_video.py --help
```

## Output and performance

More columns preserve detail but increase rendering and encoding time. Font choice, character aspect ratio, output resolution, and source codec all affect the result. Audio muxing is best-effort; when FFmpeg is unavailable, the rendered video is produced without copied audio.

No sample movies are included because the original local test clips were not redistributable. Use media you own or are licensed to process.

## License

Original source is MIT licensed. OpenCV, Pillow, NumPy, tqdm, FFmpeg, and installed codecs retain their upstream licenses.
