"""
Stitch multiple video files together, in the order given.

Strategy:
  1. Probe every input.
  2. If all inputs share video codec, resolution, pixel format, and (roughly)
     frame rate -> use the concat DEMUXER with stream copy. No re-encoding,
     so this is essentially as fast as disk I/O allows.
  3. Otherwise -> use the concat FILTER, re-encoding every input to match
     the first input's resolution/fps (others are scaled + letterboxed to
     preserve aspect ratio). Any input missing audio gets a silent track
     generated so all streams line up.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable, Optional

from .ffmpeg_utils import StreamInfo, probe, run_ffmpeg


class IncompatibleInputsError(Exception):
    pass


def _fps_close(a: Optional[float], b: Optional[float], tol: float = 0.05) -> bool:
    if a is None or b is None:
        return a == b
    return abs(a - b) <= tol * max(a, b)


def check_compatibility(infos: list[StreamInfo]) -> bool:
    """True if a fast stream-copy concat is safe."""
    if len(infos) < 2:
        return True
    first = infos[0]
    for other in infos[1:]:
        if other.v_codec != first.v_codec:
            return False
        if other.width != first.width or other.height != first.height:
            return False
        if other.pix_fmt != first.pix_fmt:
            return False
        if not _fps_close(other.fps, first.fps):
            return False
        if other.has_audio != first.has_audio:
            return False
        if other.has_audio and first.has_audio:
            if other.a_codec != first.a_codec or other.a_sample_rate != first.a_sample_rate:
                return False
    return True


def _escape_concat_path(p: Path) -> str:
    # ffmpeg concat demuxer escaping: a literal ' becomes '\''
    s = str(p.resolve())
    return s.replace("'", "'\\''")


def stitch_fast(inputs: list[Path], output: Path) -> None:
    """Stream-copy concat via the concat demuxer. Requires compatible inputs."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        for p in inputs:
            f.write(f"file '{_escape_concat_path(p)}'\n")
        list_path = f.name

    try:
        run_ffmpeg([
            "-f", "concat", "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            str(output),
        ])
    finally:
        Path(list_path).unlink(missing_ok=True)


def stitch_reencode(
    inputs: list[Path],
    output: Path,
    infos: list[StreamInfo],
    on_progress: Optional[Callable[[float], None]] = None,
    crf: int = 18,
) -> None:
    """
    Re-encoding concat via filter_complex. Normalizes every input to the
    first input's resolution and frame rate; adds silent audio to any
    input that lacks it.
    """
    target_w = infos[0].width or 1920
    target_h = infos[0].height or 1080
    target_fps = infos[0].fps or 30.0
    any_audio = any(i.has_audio for i in infos)

    args: list[str] = []
    for p in inputs:
        args += ["-i", str(p)]

    filter_parts = []
    concat_inputs = []
    for idx, info in enumerate(infos):
        v_label = f"v{idx}"
        filter_parts.append(
            f"[{idx}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={target_fps}[{v_label}]"
        )
        concat_inputs.append(f"[{v_label}]")

        if any_audio:
            a_label = f"a{idx}"
            if info.has_audio:
                filter_parts.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo[{a_label}]")
            else:
                dur = max(info.duration_sec, 0.1)
                filter_parts.append(
                    f"anullsrc=channel_layout=stereo:sample_rate=48000,"
                    f"atrim=duration={dur}[{a_label}]"
                )
            concat_inputs.append(f"[{a_label}]")

    n = len(infos)
    v_flag = 1
    a_flag = 1 if any_audio else 0
    concat_str = "".join(concat_inputs) + f"concat=n={n}:v={v_flag}:a={a_flag}[outv]" + ("[outa]" if any_audio else "")
    filter_complex = ";".join(filter_parts) + ";" + concat_str

    args += ["-filter_complex", filter_complex, "-map", "[outv]"]
    if any_audio:
        args += ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k"]
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), str(output)]

    total_duration = sum(i.duration_sec for i in infos)
    run_ffmpeg(args, duration_hint=total_duration, on_progress=on_progress)


def stitch(
    input_paths: list[str | Path],
    output_path: str | Path,
    on_progress: Optional[Callable[[float], None]] = None,
    force_reencode: bool = False,
) -> str:
    """
    Stitch inputs in the given order into output_path.
    Returns "fast" or "reencode" indicating which path was taken.
    """
    inputs = [Path(p) for p in input_paths]
    output = Path(output_path)

    if len(inputs) < 2:
        raise ValueError("Need at least 2 input videos to stitch.")
    for p in inputs:
        if not p.exists():
            raise FileNotFoundError(f"Input not found: {p}")

    infos = [probe(p) for p in inputs]

    if not force_reencode and check_compatibility(infos):
        if on_progress:
            on_progress(0.0)
        stitch_fast(inputs, output)
        if on_progress:
            on_progress(1.0)
        return "fast"
    else:
        stitch_reencode(inputs, output, infos, on_progress=on_progress)
        return "reencode"
