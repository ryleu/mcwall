"""The terminal front end: watch a render, drive every command.

Three things are kept apart on purpose. `state` gathers facts, `layout`
arranges them, and this module is the only part that talks to curses or
spawns anything — so the awkward-to-test half stays small.

Commands run as `python -m mcwall.cli ...` subprocesses rather than by calling
the functions directly. A render takes hours and a crash inside it must not
take the window down with it, and a subprocess can be stopped from here
without unwinding our own stack.
"""

from __future__ import annotations

import curses
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from . import layout, state
from .layout import Line

REFRESH_SECONDS = 1.0
SLOW_REFRESH_SECONDS = 10.0
PREVIEW_COLUMNS = 40


# ------------------------------------------------------------------ snapshot


@dataclass
class Snapshot:
    build_key: str | None = None
    world: str | None = None
    seed: int | None = None
    camera: tuple | None = None
    active_build: str | None = None
    progress: state.Progress | None = None
    jobs: state.JobView = field(default_factory=state.JobView)
    last_image: Path | None = None
    client_running: bool = False
    worlds: list = field(default_factory=list)
    problem: str | None = None


def gather(config, jobs_cache: dict) -> Snapshot:
    """Read everything the screen needs, surviving a half-configured setup.

    Any of these can legitimately fail — no instance yet, a world never
    entered, a library not built — and none of them should stop the rest of
    the window from drawing.
    """
    from .. import producer, session
    from ..library import Manifest

    snapshot = Snapshot(world=config.render.world)

    try:
        game = producer.game_dir(config)
    except Exception as problem:  # noqa: BLE001
        snapshot.problem = str(problem)
        return snapshot

    try:
        snapshot.build_key = producer.pack_id(config)
    except Exception as problem:  # noqa: BLE001 - usually "no saved camera yet"
        snapshot.problem = str(problem).splitlines()[0]

    try:
        snapshot.seed = producer.world_seed(config)
    except Exception:  # noqa: BLE001
        pass
    try:
        snapshot.camera = producer.render_camera(config)
    except Exception:  # noqa: BLE001
        pass
    try:
        snapshot.active_build = Manifest.read(config.library.root).pack_id
    except Exception:  # noqa: BLE001
        pass

    state_dir = game / "mcwall"
    snapshot.progress = state.read_progress(state_dir)

    jobs_path = state_dir / state.JOBS_NAME
    try:
        stamp = jobs_path.stat().st_mtime
    except OSError:
        stamp = None
    if stamp is not None and jobs_cache.get("stamp") != stamp:
        jobs_cache["stamp"] = stamp
        jobs_cache["jobs"] = state.read_jobs(state_dir)
    jobs = jobs_cache.get("jobs", [])

    current = snapshot.progress.current if snapshot.progress else None
    snapshot.jobs = state.job_view(jobs, current)
    snapshot.last_image = state.job_image(
        snapshot.jobs.previous or snapshot.jobs.current,
        config.library.root,
        config.library.root / ".staging",
        config.library.image_format,
    )

    try:
        snapshot.client_running = bool(producer.running_clients(config))
    except Exception:  # noqa: BLE001
        pass

    try:
        snapshot.worlds = state.world_rows(
            session.worlds(game),
            session.read_seed,
            session.read_camera,
            config.render.world,
        )
    except Exception:  # noqa: BLE001
        snapshot.worlds = []
    return snapshot


# -------------------------------------------------------------------- runner


class Runner:
    """One mcwall command at a time, with its output kept for the log pane."""

    def __init__(self, limit: int = 2000):
        self.lines: deque[str] = deque(maxlen=limit)
        self.command: str | None = None
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, argv: list[str]) -> bool:
        if self.busy:
            return False
        self.command = " ".join(argv)
        self.lines.append(f"$ mcwall {self.command}")
        process = subprocess.Popen(
            [sys.executable, "-m", "mcwall.cli", *argv],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            self._process = process
        threading.Thread(target=self._pump, args=(process,), daemon=True).start()
        return True

    def _pump(self, process: subprocess.Popen) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            self.lines.append(line.rstrip())
        code = process.wait()
        self.lines.append(f"[exit {code}]")

    def stop(self) -> None:
        """Ask the command to stop. A render is resumable, so this is safe."""
        with self._lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
            self.lines.append("[terminating]")


# ------------------------------------------------------------------- preview


def preview_pixels(path: Path, columns: int, rows: int):
    """Downscale an image to a grid of RGB triples using ffmpeg.

    ffmpeg is already a dependency of the conversion step, so this adds
    nothing to the environment. Returns None when it is unavailable or the
    frame cannot be read, and the caller simply draws no picture.
    """
    if not shutil.which("ffmpeg"):
        return None
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-loglevel", "error", "-i", str(path),
                "-vf", f"scale={columns}:{rows}",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    data = result.stdout
    if len(data) < columns * rows * 3:
        return None
    grid = []
    for row in range(rows):
        line = []
        for column in range(columns):
            offset = (row * columns + column) * 3
            line.append((data[offset], data[offset + 1], data[offset + 2]))
        grid.append(line)
    return grid


def _cube(value: int) -> int:
    """Map a channel onto the xterm 6x6x6 colour cube."""
    return min(5, max(0, (value * 6) // 256))


# ----------------------------------------------------------------------- app


class App:
    VIEWS = ("dashboard", "worlds", "commands", "output")

    def __init__(self, config, specs):
        self.config = config
        self.specs = specs
        self.view = "dashboard"
        self.runner = Runner()
        self.snapshot = Snapshot()
        self._jobs_cache: dict = {}
        self._last_fast = 0.0
        self.world_cursor = 0
        self.command_cursor = 0
        self.form: dict | None = None
        self.show_preview = True
        self._preview_for: Path | None = None
        self._preview = None
        self.message: str | None = None

    # -- data -------------------------------------------------------------

    def refresh(self, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_fast >= REFRESH_SECONDS:
            self._last_fast = now
            self.snapshot = gather(self.config, self._jobs_cache)

    def reload_config(self) -> None:
        from .. import config as config_mod

        try:
            self.config = config_mod.load()
        except Exception as problem:  # noqa: BLE001
            self.message = str(problem)

    # -- drawing ----------------------------------------------------------

    def body(self, width: int) -> list[Line]:
        if self.form is not None:
            spec = self.form["spec"]
            return layout.command_form(
                spec, self.form["values"], self.form["cursor"],
                state.build_argv(spec.name, self.form["values"]),
            )
        if self.view == "dashboard":
            lines = layout.dashboard(self.snapshot, width)
            if self.snapshot.problem:
                lines.append(Line(""))
                lines.append(Line(f"  {self.snapshot.problem}", layout.WARN))
            return lines
        if self.view == "worlds":
            return layout.worlds(self.snapshot.worlds, self.world_cursor)
        if self.view == "commands":
            return layout.commands(self.specs, self.command_cursor)
        return layout.log(self.runner.lines, self.runner.command
                          if self.runner.busy else None, 200)

    def draw(self, screen, styles) -> None:
        screen.erase()
        height, width = screen.getmaxyx()

        tabs = "  ".join(
            f"[{name}]" if name == self.view and self.form is None else f" {name} "
            for name in self.VIEWS
        )
        screen.addnstr(0, 0, tabs.ljust(width - 1), width - 1, styles[layout.TITLE])

        picture_width = 0
        picture = self._picture()
        if picture:
            picture_width = len(picture[0]) + 2

        lines = self.body(max(20, width - picture_width - 2))
        area = height - 3
        for index, line in enumerate(lines[:area]):
            screen.addnstr(
                2 + index, 1, line.text, max(1, width - picture_width - 2),
                styles.get(line.style, styles[layout.PLAIN]),
            )

        if picture:
            self._draw_picture(screen, picture, width - picture_width + 1)

        footer = layout.help_bar(
            "form" if self.form is not None else self.view, self.runner.busy
        )
        if self.message:
            footer = self.message
        screen.addnstr(height - 1, 0, footer.ljust(width - 1), width - 1,
                       styles[layout.DIM])
        screen.refresh()

    def _picture(self):
        if not self.show_preview or self.form is not None:
            return None
        if self.view not in ("dashboard", "output"):
            return None
        path = self.snapshot.last_image
        if not path or not path.exists():
            return None
        if path != self._preview_for:
            self._preview_for = path
            self._preview = preview_pixels(
                path, PREVIEW_COLUMNS, max(6, PREVIEW_COLUMNS // 4)
            )
        return self._preview

    def _draw_picture(self, screen, picture, left: int) -> None:
        if left < 1 or not curses.has_colors() or curses.COLORS < 256:
            return
        for row, cells in enumerate(picture):
            for column, (red, green, blue) in enumerate(cells):
                index = 16 + 36 * _cube(red) + 6 * _cube(green) + _cube(blue)
                try:
                    screen.addstr(2 + row, left + column, " ",
                                  curses.color_pair(200 + index - 16))
                except curses.error:
                    return

    # -- input ------------------------------------------------------------

    def prompt(self, screen, question: str, initial: str = "") -> str | None:
        height, width = screen.getmaxyx()
        curses.echo()
        curses.curs_set(1)
        screen.addnstr(height - 1, 0, (question + " ").ljust(width - 1), width - 1)
        screen.move(height - 1, len(question) + 1)
        try:
            typed = screen.getstr(height - 1, len(question) + 1, 120)
        except curses.error:
            typed = None
        finally:
            curses.noecho()
            curses.curs_set(0)
        if typed is None:
            return None
        text = typed.decode(errors="replace").strip()
        return text or initial

    def handle(self, screen, key: int) -> bool:
        """Returns False to quit."""
        self.message = None

        if self.form is not None:
            return self._handle_form(screen, key)

        if key in (ord("q"), 27):
            if self.runner.busy:
                self.message = "a command is still running — press x to stop it first"
                return True
            return False
        if key == ord("d"):
            self.view = "dashboard"
        elif key == ord("w"):
            self.view = "worlds"
        elif key == ord("c"):
            self.view = "commands"
        elif key == ord("o"):
            self.view = "output"
        elif key == ord("i"):
            self.show_preview = not self.show_preview
        elif key == ord("x"):
            self.runner.stop()
        elif key == ord("r") and self.view == "dashboard":
            self._run(["render"])
        elif key == ord("e") and self.view in ("dashboard", "worlds"):
            self._run(["edit"])
        elif key in (curses.KEY_UP, ord("k")):
            self._move(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            self._move(1)
        elif key in (curses.KEY_ENTER, 10, 13):
            self._activate()
        return True

    def _move(self, delta: int) -> None:
        if self.view == "worlds" and self.snapshot.worlds:
            self.world_cursor = max(
                0, min(len(self.snapshot.worlds) - 1, self.world_cursor + delta)
            )
        elif self.view == "commands" and self.specs:
            self.command_cursor = max(
                0, min(len(self.specs) - 1, self.command_cursor + delta)
            )

    def _activate(self) -> None:
        if self.view == "worlds" and self.snapshot.worlds:
            row = self.snapshot.worlds[self.world_cursor]
            from .. import config as config_mod

            self.config = config_mod.replace_render(self.config, world=row.name)
            config_mod.update("render", {"world": row.name})
            self.message = f"rendering from {row.name}"
            self.refresh(force=True)
        elif self.view == "commands" and self.specs:
            spec = self.specs[self.command_cursor]
            self.form = {
                "spec": spec,
                "cursor": 0,
                "values": {
                    option.flags[-1]: (False if option.kind == "flag" else None)
                    for option in spec.options
                },
            }

    def _handle_form(self, screen, key: int) -> bool:
        form = self.form
        spec = form["spec"]
        if key == 27:
            self.form = None
        elif key in (curses.KEY_UP, ord("k")):
            form["cursor"] = max(0, form["cursor"] - 1)
        elif key in (curses.KEY_DOWN, ord("j")):
            form["cursor"] = min(max(0, len(spec.options) - 1), form["cursor"] + 1)
        elif key == ord(" ") and spec.options:
            option = spec.options[form["cursor"]]
            if option.kind == "flag":
                form["values"][option.flags[-1]] = not form["values"][option.flags[-1]]
        elif key in (curses.KEY_ENTER, 10, 13) and spec.options:
            option = spec.options[form["cursor"]]
            if option.kind == "flag":
                form["values"][option.flags[-1]] = not form["values"][option.flags[-1]]
            else:
                hint = f"{option.flags[-1]}"
                if option.choices:
                    hint += f" ({'/'.join(option.choices)})"
                typed = self.prompt(screen, f"{hint}:")
                if typed is not None:
                    form["values"][option.flags[-1]] = typed
        elif key == ord("x"):
            argv = state.build_argv(spec.name, form["values"])
            self.form = None
            self._run(argv)
        return True

    def _run(self, argv: list[str]) -> None:
        if not self.runner.start(argv):
            self.message = "a command is already running"
            return
        self.view = "output"


def run(config, specs) -> int:
    app = App(config, specs)

    def main(screen):
        curses.curs_set(0)
        screen.timeout(int(REFRESH_SECONDS * 1000))
        styles = {layout.PLAIN: curses.A_NORMAL}
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                pass
            pairs = {
                layout.TITLE: (curses.COLOR_CYAN, 1),
                layout.DIM: (curses.COLOR_BLUE, 2),
                layout.OK: (curses.COLOR_GREEN, 3),
                layout.WARN: (curses.COLOR_YELLOW, 4),
                layout.ERR: (curses.COLOR_RED, 5),
                layout.SEL: (curses.COLOR_MAGENTA, 6),
                layout.BAR: (curses.COLOR_GREEN, 7),
            }
            for name, (colour, index) in pairs.items():
                curses.init_pair(index, colour, -1)
                styles[name] = curses.color_pair(index)
            styles[layout.TITLE] |= curses.A_BOLD
            styles[layout.SEL] |= curses.A_REVERSE
            if curses.COLORS >= 256:
                for index in range(216):
                    try:
                        curses.init_pair(200 + index, -1, 16 + index)
                    except curses.error:
                        break

        while True:
            app.refresh()
            app.draw(screen, styles)
            key = screen.getch()
            if key == -1:
                continue
            if key == curses.KEY_RESIZE:
                continue
            if not app.handle(screen, key):
                break

    curses.wrapper(main)
    return 0
