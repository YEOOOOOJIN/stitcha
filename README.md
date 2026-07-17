# video-toolkit

Two workflows, wrapped around FFmpeg:

1. **Stitch** — join multiple video files together, in the order given.
2. **Compress** — shrink a video (mov/mp4/webm/etc.) to under a target file size in MB.

Run with no arguments to get an interactive terminal UI. Run with arguments for
scripted/batch use.

## Setup (Windows)

1. **Install Python 3.11+** if you don't have it: https://www.python.org/downloads/
   During install, check "Add python.exe to PATH".

2. **Install FFmpeg** (this does the actual video work):
   ```
   winget install --id=Gyan.FFmpeg -e
   ```
   Close and reopen your terminal afterward so the PATH change takes effect.
   Verify with:
   ```
   ffmpeg -version
   ```

3. **Install the Python dependencies**, from this project folder:
   ```
   pip install -r requirements.txt
   ```

## Usage

### Interactive mode

```
python main.py
```

Opens a menu-driven terminal interface. Add clip paths one at a time (order
matters — they're joined in the order you add them), or fill in a single
input/output/target-size for compression. Progress is shown live.

### CLI mode

Stitch clips together, in the order listed:
```
python main.py stitch -o combined.mp4 clip1.mp4 clip2.mp4 clip3.mp4
```

Compress a video to under a target size:
```
python main.py compress -i input.mov -o output.mp4 --max-size 25
```

`--max-size` is in decimal MB (1 MB = 1,000,000 bytes) — the same convention
most upload limits (Discord, email, etc.) use.

## How it works

**Stitching**: every input is probed with `ffprobe` first. If they all share
the same video codec, resolution, pixel format, and frame rate, the files are
joined with FFmpeg's concat demuxer using stream copy — no re-encoding, so
it's close to instant regardless of clip length. If any input differs, it
falls back to re-encoding everything to match the *first* clip's resolution
and frame rate (other clips are scaled and letterboxed to preserve aspect
ratio, not stretched or cropped).

**Compression**: two-pass H.264 encoding. The target video bitrate is
computed from your target size and the clip's duration, with a small safety
margin built in. If the actual output still lands over your target size
(bitrate estimation is never perfectly exact), it automatically retries at a
lower bitrate, up to a few attempts. If your target size is extremely small
relative to the clip's length, there's a floor on how low the bitrate will
go — below that, further shrinking would produce an unwatchable result, and
the tool will tell you it couldn't fully hit the target rather than
silently producing garbage.

## Notes

- Building a standalone `.exe` (so this runs without a Python install) is
  possible via PyInstaller (`pip install pyinstaller`, then
  `pyinstaller --onefile main.py`), but FFmpeg itself still needs to be on
  PATH separately either way — it's a large native binary that isn't
  bundled by this project.
- If ffmpeg/ffprobe aren't found on PATH, both the CLI and TUI will tell you
  clearly rather than failing with a cryptic error.
