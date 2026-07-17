"""
Interactive terminal UI, launched when the tool is run with no arguments.
"""
from __future__ import annotations

from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button, Footer, Header, Input, Label, ListView, ListItem,
    ProgressBar, RichLog, Static,
)

from core.stitch import stitch
from core.compress import compress
from core.ffmpeg_utils import FFmpegNotFoundError, check_ffmpeg_available


def _clean_path(raw: str) -> str:
    r"""Strip PowerShell drag-and-drop artifacts like: & 'd:\path with spaces\file.mp4'"""
    s = raw.strip()
    if s.startswith("& "):
        s = s[2:]
    s = s.strip().strip("'").strip('"')
    return s.strip()

APP_CSS = """
Screen {
    background: $surface;
}

#menu-panel {
    align: center middle;
    height: 100%;
}

#menu-title {
    text-style: bold;
    color: $accent;
    padding-bottom: 1;
}

#menu-buttons {
    width: 40;
}

#menu-buttons Button {
    width: 100%;
    margin-bottom: 1;
}

.form-panel {
    padding: 1 2;
    height: 100%;
}

.form-row {
    height: 3;
    margin-bottom: 1;
}

.form-row Label {
    width: 18;
    padding-top: 1;
}

.form-row Input {
    width: 1fr;
}

#file-list {
    height: 8;
    border: round $primary;
    margin-bottom: 1;
}

#log {
    border: round $primary;
    height: 1fr;
    margin-top: 1;
}

.action-row {
    height: 3;
    margin-bottom: 1;
}

.action-row Button {
    margin-right: 2;
}

ProgressBar {
    margin-bottom: 1;
}
"""


class MainMenu(Screen):
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="menu-panel"):
            yield Static("stitcha", id="menu-title")
            with Vertical(id="menu-buttons"):
                yield Button("Stitch videos together", id="goto-stitch", variant="primary")
                yield Button("Compress a video", id="goto-compress", variant="primary")
                yield Button("Quit", id="quit")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "goto-stitch":
            self.app.push_screen(StitchScreen())
        elif event.button.id == "goto-compress":
            self.app.push_screen(CompressScreen())
        elif event.button.id == "quit":
            self.app.exit()


class StitchScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def __init__(self) -> None:
        super().__init__()
        self._paths: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(classes="form-panel"):
            yield Static("Stitch Videos  -  clips are joined in the order shown below", classes="menu-title")
            with Horizontal(classes="form-row"):
                yield Label("Add clip path:")
                yield Input(placeholder="C:\\videos\\clip1.mp4", id="path-input")
                yield Button("Add", id="add-path")
            yield ListView(id="file-list")
            with Horizontal(classes="action-row"):
                yield Button("Remove selected", id="remove-path")
                yield Button("Clear all", id="clear-paths")
            with Horizontal(classes="form-row"):
                yield Label("Output path:")
                yield Input(placeholder="C:\\videos\\stitched.mp4", id="output-input")
            with Horizontal(classes="action-row"):
                yield Button("Run stitch", id="run-stitch", variant="success")
                yield Button("Back", id="back")
            yield ProgressBar(id="progress", total=100)
            yield RichLog(id="log", wrap=True)
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        log = self.query_one("#log", RichLog)
        if event.button.id == "add-path":
            path_input = self.query_one("#path-input", Input)
            value = _clean_path(path_input.value)
            if value:
                self._paths.append(value)
                self.query_one("#file-list", ListView).append(
                    ListItem(Label(f"{len(self._paths)}. {value}"))
                )
                path_input.value = ""
        elif event.button.id == "remove-path":
            file_list = self.query_one("#file-list", ListView)
            idx = file_list.index
            if idx is not None and 0 <= idx < len(self._paths):
                del self._paths[idx]
                file_list.remove_items([idx])
                self._renumber()
        elif event.button.id == "clear-paths":
            self._paths.clear()
            self.query_one("#file-list", ListView).clear()
        elif event.button.id == "back":
            self.app.pop_screen()
        elif event.button.id == "run-stitch":
            output = _clean_path(self.query_one("#output-input", Input).value)
            if len(self._paths) < 2:
                log.write("[red]Need at least 2 clips.[/red]")
                return
            if not output:
                log.write("[red]Output path is required.[/red]")
                return
            self.run_stitch_job(list(self._paths), output)

    def _renumber(self) -> None:
        file_list = self.query_one("#file-list", ListView)
        for i, (item, p) in enumerate(zip(file_list.children, self._paths), start=1):
            label = item.query_one(Label)
            label.update(f"{i}. {p}")

    @work(thread=True)
    def run_stitch_job(self, paths: list[str], output: str) -> None:
        log = self.query_one("#log", RichLog)
        progress = self.query_one("#progress", ProgressBar)
        self.app.call_from_thread(log.write, f"Stitching {len(paths)} clip(s)...")

        def on_progress(frac: float):
            self.app.call_from_thread(progress.update, progress=frac * 100)

        try:
            method = stitch(paths, output, on_progress=on_progress)
        except Exception as e:
            self.app.call_from_thread(log.write, f"[red]Failed: {e}[/red]")
            return
        if method == "fast":
            self.app.call_from_thread(log.write, "[green]Done - fast stream copy (clips were compatible).[/green]")
        else:
            self.app.call_from_thread(log.write, "[green]Done - re-encoded (clips differed in format).[/green]")


class CompressScreen(Screen):
    BINDINGS = [("escape", "app.pop_screen", "Back")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with VerticalScroll(classes="form-panel"):
            yield Static("Compress Video", classes="menu-title")
            with Horizontal(classes="form-row"):
                yield Label("Input path:")
                yield Input(placeholder="C:\\videos\\input.mov", id="input-path")
            with Horizontal(classes="form-row"):
                yield Label("Output path:")
                yield Input(placeholder="C:\\videos\\output.mp4", id="output-path")
            with Horizontal(classes="form-row"):
                yield Label("Max size (MB):")
                yield Input(placeholder="25", id="max-size")
            with Horizontal(classes="action-row"):
                yield Button("Run compress", id="run-compress", variant="success")
                yield Button("Back", id="back")
            yield ProgressBar(id="progress", total=100)
            yield RichLog(id="log", wrap=True)
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        log = self.query_one("#log", RichLog)
        if event.button.id == "back":
            self.app.pop_screen()
            return
        if event.button.id != "run-compress":
            return

        input_path = _clean_path(self.query_one("#input-path", Input).value)
        output_path = _clean_path(self.query_one("#output-path", Input).value)
        max_size_str = self.query_one("#max-size", Input).value.strip()

        if not input_path or not output_path or not max_size_str:
            log.write("[red]All fields are required.[/red]")
            return
        try:
            max_size = float(max_size_str)
        except ValueError:
            log.write("[red]Max size must be a number.[/red]")
            return

        self.run_compress_job(input_path, output_path, max_size)

    @work(thread=True)
    def run_compress_job(self, input_path: str, output_path: str, max_size: float) -> None:
        log = self.query_one("#log", RichLog)
        progress = self.query_one("#progress", ProgressBar)
        self.app.call_from_thread(log.write, f"Compressing to under {max_size} MB...")

        def on_progress(frac: float):
            self.app.call_from_thread(progress.update, progress=frac * 100)

        try:
            result = compress(input_path, output_path, max_size, on_progress=on_progress)
        except Exception as e:
            self.app.call_from_thread(log.write, f"[red]Failed: {e}[/red]")
            return

        size_mb = result.output_size_bytes / 1_000_000
        if result.met_target:
            self.app.call_from_thread(
                log.write,
                f"[green]Done - {size_mb:.2f} MB (target {max_size} MB), "
                f"{result.attempts} pass(es), ~{result.final_video_kbps:.0f} kbps video.[/green]",
            )
        else:
            self.app.call_from_thread(
                log.write,
                f"[yellow]Still over target: {size_mb:.2f} MB after {result.attempts} attempts "
                f"(hit {result.final_video_kbps:.0f} kbps floor). Target may not be feasible "
                f"for this clip's length.[/yellow]",
            )


class VideoToolkitApp(App):
    CSS = APP_CSS
    TITLE = "stitcha"

    def on_mount(self) -> None:
        try:
            check_ffmpeg_available()
        except FFmpegNotFoundError as e:
            self.exit(message=str(e))
            return
        self.push_screen(MainMenu())


def run() -> None:
    VideoToolkitApp().run()


if __name__ == "__main__":
    run()
