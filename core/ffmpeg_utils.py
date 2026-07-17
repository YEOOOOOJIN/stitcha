"""
Low-level wrappers around the ffmpeg / ffprobe binaries.
Everything else in this project talks to video files through this module.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional


class FFmpegNotFoundError(RuntimeError):
    pass


def check_ffmpeg_available() -> None:
    """Raise a clear error if ffmpeg/ffprobe aren't on PATH."""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise FFmpegNotFoundError(
            "Missing required tool(s) on PATH: "
            + ", ".join(missing)
            + ".\n\nOn Windows, install FFmpeg with:\n"
            "    winget install --id=Gyan.FFmpeg -e\n"
            "then restart your terminal so PATH updates take effect.\n"
            "Verify with: ffmpeg -version"
        )


@dataclass
class StreamInfo:
    duration_sec: float
    size_bytes: int
    v_codec: Optional[str]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    pix_fmt: Optional[str]
    a_codec: Optional[str]
    a_sample_rate: Optional[int]
    a_channels: Optional[int]
    has_audio: bool


def _parse_frame_rate(rate_str: Optional[str]) -> Optional[float]:
    if not rate_str or rate_str == "0/0":
        return None
    if "/" in rate_str:
        num, den = rate_str.split("/")
        den = float(den)
        return float(num) / den if den else None
    return float(rate_str)


def probe(path: str | Path) -> StreamInfo:
    """Run ffprobe on a file and return the info we care about."""
    path = str(path)
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}:\n{result.stderr}")

    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])

    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(fmt.get("duration", 0.0)) if fmt.get("duration") else 0.0
    if not duration and v_stream and v_stream.get("duration"):
        duration = float(v_stream["duration"])

    return StreamInfo(
        duration_sec=duration,
        size_bytes=int(fmt.get("size", 0)) if fmt.get("size") else Path(path).stat().st_size,
        v_codec=v_stream.get("codec_name") if v_stream else None,
        width=v_stream.get("width") if v_stream else None,
        height=v_stream.get("height") if v_stream else None,
        fps=_parse_frame_rate(v_stream.get("avg_frame_rate")) if v_stream else None,
        pix_fmt=v_stream.get("pix_fmt") if v_stream else None,
        a_codec=a_stream.get("codec_name") if a_stream else None,
        a_sample_rate=int(a_stream["sample_rate"]) if a_stream and a_stream.get("sample_rate") else None,
        a_channels=a_stream.get("channels") if a_stream else None,
        has_audio=a_stream is not None,
    )


def run_ffmpeg(
    args: list[str],
    duration_hint: Optional[float] = None,
    on_progress: Optional[Callable[[float], None]] = None,
) -> None:
    """
    Run ffmpeg with -progress piped to stdout so we can report fractional
    progress (0.0-1.0) back to a caller (CLI progress bar / TUI widget).
    `args` should be the ffmpeg args WITHOUT the leading 'ffmpeg' and
    WITHOUT -progress/-nostats (those are added here).
    """
    cmd = ["ffmpeg", "-y", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1
    )

    # ffmpeg writes progress info to stdout and its (often verbose) normal log
    # output to stderr. Both are OS pipes with a bounded buffer (small on
    # Windows, ~64KB). If we only read stdout here, a long-running encode can
    # fill the stderr buffer; ffmpeg then blocks writing to stderr while we
    # block waiting on stdout, and neither side can proceed. Draining stderr
    # concurrently on a background thread avoids that deadlock. The deque cap
    # bounds memory on very long/chatty encodes; only the tail is ever used
    # for error reporting anyway.
    stderr_lines: deque[str] = deque(maxlen=200)

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if on_progress and duration_hint and line.startswith("out_time_ms="):
            try:
                out_time_ms = int(line.split("=", 1)[1])
                frac = min(1.0, (out_time_ms / 1_000_000) / duration_hint)
                on_progress(frac)
            except (ValueError, ZeroDivisionError):
                pass
        elif on_progress and line.startswith("progress=") and line.endswith("end"):
            on_progress(1.0)

    proc.wait()
    stderr_thread.join()

    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg exited with code {proc.returncode}:\n"
            + "".join(list(stderr_lines)[-40:])
        )
