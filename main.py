"""
Entry point.

    python main.py                          -> launches the interactive TUI
    python main.py stitch -o out.mp4 a.mp4 b.mp4   -> flag-driven CLI mode
    python main.py compress -i in.mov -o out.mp4 --max-size 25
"""
import sys


def main() -> int:
    if len(sys.argv) == 1:
        from tui import run
        run()
        return 0
    else:
        from cli import main as cli_main
        return cli_main(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
