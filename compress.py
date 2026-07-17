"""
Compress a video to land under a target file size, using two-pass H.264
encoding. The video bitrate is computed from the target size and the
clip's duration; if the result still overshoots (bitrate estimation is
never perfectly exact), it automatically retries at a reduced bitrate.
"""
from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .ffmpeg_utils import probe, run_ffmpeg

NULL_DEVICE = "NUL" if os.name == "nt" else "/dev/null"

MIN_VIDEO_KBPS = 100
DEFAULT_AUDIO_KBPS = 128
FLOOR_AUDIO_KBPS = 64
SAFETY_MARGIN = 0.93  # target this fraction of the requested size, to leave headroom
MAX_ATTEMPTS = 4
RETRY_SHRINK_FACTOR = 0.85


@dataclass
class CompressResult:
    output_path: str
    attempts: int
    final_video_kbps: float
    final_audio_kbps: int
    output_size_bytes: int
    target_size_bytes: int
    met_target: bool


def compress(
    input_path: str | Path,
    output_path: str | Path,
    target_size_mb: float,
    on_progress: Optional[Callable[[float], None]] = None,
) -> CompressResult:
    input_path = Path(input_path)
    output_path = Path(output_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input not found: {input_path}")

    info = probe(input_path)
    if info.duration_sec <= 0:
        raise RuntimeError(f"Could not determine duration of {input_path}")

    target_bytes_requested = target_size_mb * 1_000_000
    target_bytes_working = target_bytes_requested * SAFETY_MARGIN

    audio_kbps = DEFAULT_AUDIO_KBPS if info.has_audio else 0
    video_kbps = (target_bytes_working * 8 / 1000) / info.duration_sec - audio_kbps

    if info.has_audio and video_kbps < MIN_VIDEO_KBPS:
        audio_kbps = FLOOR_AUDIO_KBPS
        video_kbps = (target_bytes_working * 8 / 1000) / info.duration_sec - audio_kbps

    if video_kbps < MIN_VIDEO_KBPS:
        video_kbps = MIN_VIDEO_KBPS  # floor; target may simply not be achievable

    passlog_prefix = str(Path(tempfile.gettempdir()) / f"vtk_pass_{uuid.uuid4().hex}")

    attempts = 0
    final_size = 0
    met_target = False

    try:
        while attempts < MAX_ATTEMPTS:
            attempts += 1

            def progress_pass1(frac: float, _a=attempts):
                if on_progress:
                    on_progress(((_a - 1) / MAX_ATTEMPTS) + (frac * 0.5) / MAX_ATTEMPTS)

            def progress_pass2(frac: float, _a=attempts):
                if on_progress:
                    on_progress(((_a - 1) / MAX_ATTEMPTS) + (0.5 + frac * 0.5) / MAX_ATTEMPTS)

            # Pass 1
            run_ffmpeg(
                [
                    "-i", str(input_path),
                    "-c:v", "libx264", "-b:v", f"{video_kbps:.0f}k",
                    "-pass", "1", "-passlogfile", passlog_prefix,
                    "-an", "-f", "mp4", NULL_DEVICE,
                ],
                duration_hint=info.duration_sec,
                on_progress=progress_pass1,
            )

            # Pass 2
            pass2_args = [
                "-i", str(input_path),
                "-c:v", "libx264", "-b:v", f"{video_kbps:.0f}k",
                "-pass", "2", "-passlogfile", passlog_prefix,
            ]
            if info.has_audio and audio_kbps > 0:
                pass2_args += ["-c:a", "aac", "-b:a", f"{audio_kbps}k"]
            else:
                pass2_args += ["-an"]
            pass2_args += [str(output_path)]

            run_ffmpeg(pass2_args, duration_hint=info.duration_sec, on_progress=progress_pass2)

            final_size = output_path.stat().st_size
            if final_size <= target_bytes_requested:
                met_target = True
                break

            video_kbps *= RETRY_SHRINK_FACTOR
            if video_kbps < MIN_VIDEO_KBPS:
                video_kbps = MIN_VIDEO_KBPS
    finally:
        for suffix in ("-0.log", "-0.log.mbtree"):
            Path(passlog_prefix + suffix).unlink(missing_ok=True)

    if on_progress:
        on_progress(1.0)

    return CompressResult(
        output_path=str(output_path),
        attempts=attempts,
        final_video_kbps=video_kbps,
        final_audio_kbps=audio_kbps,
        output_size_bytes=final_size,
        target_size_bytes=int(target_bytes_requested),
        met_target=met_target,
    )
