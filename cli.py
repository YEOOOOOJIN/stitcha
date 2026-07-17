"""
Flag-driven CLI mode:

    videotool stitch -o out.mp4 clip1.mp4 clip2.mp4 clip3.mp4
    videotool compress -i input.mov -o output.mp4 --max-size 25
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn

from core.ffmpeg_utils import FFmpegNotFoundError, check_ffmpeg_available
from core.stitch import stitch
from core.compress import compress

console = Console()


def _run_with_progress(label: str, fn, *args, **kwargs):
    with Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        TextColumn("{task.percentage:>3.0f}%"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(label, total=100)

        def on_progress(frac: float):
            progress.update(task, completed=frac * 100)

        return fn(*args, on_progress=on_progress, **kwargs)


def cmd_stitch(args: argparse.Namespace) -> int:
    if len(args.inputs) < 2:
        console.print("[red]Need at least 2 input files to stitch.[/red]")
        return 1
    console.print(f"Stitching {len(args.inputs)} clip(s) in given order -> {args.output}")
    try:
        method = _run_with_progress("Stitching", stitch, args.inputs, args.output, force_reencode=args.reencode)
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        return 1
    if method == "fast":
        console.print("[green]Done.[/green] Inputs were compatible - used fast stream copy (no re-encode).")
    else:
        console.print("[green]Done.[/green] Inputs differed in format - re-encoded to match the first clip.")
    return 0


def cmd_compress(args: argparse.Namespace) -> int:
    console.print(f"Compressing {args.input} to under {args.max_size} MB -> {args.output}")
    try:
        result = _run_with_progress(
            "Compressing", compress, args.input, args.output, args.max_size
        )
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        return 1

    size_mb = result.output_size_bytes / 1_000_000
    if result.met_target:
        console.print(
            f"[green]Done.[/green] Output is {size_mb:.2f} MB "
            f"(target {args.max_size} MB) after {result.attempts} pass(es). "
            f"Video bitrate ~{result.final_video_kbps:.0f} kbps."
        )
    else:
        console.print(
            f"[yellow]Warning:[/yellow] output is {size_mb:.2f} MB, still over the "
            f"{args.max_size} MB target after {result.attempts} attempts. "
            f"The clip's length/target size combination may not leave enough bitrate "
            f"for a usable encode (reached the {result.final_video_kbps:.0f} kbps floor)."
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="videotool", description="Stitch and compress video files.")
    sub = parser.add_subparsers(dest="command")

    p_stitch = sub.add_parser("stitch", help="Concatenate videos in the order given.")
    p_stitch.add_argument("inputs", nargs="+", help="Input video files, in the order they should be joined.")
    p_stitch.add_argument("-o", "--output", required=True, help="Output file path.")
    p_stitch.add_argument(
        "--reencode", action="store_true",
        help="Force re-encoding even if inputs look compatible (safer, slower)."
    )
    p_stitch.set_defaults(func=cmd_stitch)

    p_compress = sub.add_parser("compress", help="Compress a video under a target file size.")
    p_compress.add_argument("-i", "--input", required=True, help="Input video file (mp4, mov, webm, etc.)")
    p_compress.add_argument("-o", "--output", required=True, help="Output file path.")
    p_compress.add_argument(
        "--max-size", type=float, required=True, dest="max_size",
        help="Target maximum size in MB (decimal, 1MB = 1,000,000 bytes)."
    )
    p_compress.set_defaults(func=cmd_compress)

    return parser


def main(argv: list[str]) -> int:
    try:
        check_ffmpeg_available()
    except FFmpegNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
