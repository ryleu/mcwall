"""Everything the TUI knows, gathered without drawing anything.

Kept free of curses so the interesting parts — progress arithmetic, which job
comes next, what the command surface looks like — can be tested without a
terminal. `layout.py` turns this into lines; `app.py` puts them on screen.

Nothing here writes. The TUI watches a render it does not own, and a monitor
that can corrupt what it monitors is worse than no monitor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATUS_NAME = "status.json"
JOBS_NAME = "jobs.jsonl"


# ----------------------------------------------------------------- progress


@dataclass(frozen=True)
class Progress:
    """What the capture mod has managed so far.

    Mirrors the mod's `status.json`, which it rewrites as each job lands.
    """

    total: int = 0
    completed: int = 0
    skipped: int = 0
    failed: int = 0
    current: str | None = None
    elapsed_seconds: float = 0.0
    done: bool = False
    updated_at: datetime | None = None

    @property
    def finished(self) -> int:
        return self.completed + self.skipped

    @property
    def fraction(self) -> float:
        return min(1.0, self.finished / self.total) if self.total else 0.0

    @property
    def per_still_seconds(self) -> float | None:
        """Seconds per capture, from the mod's own clock."""
        if not self.completed or self.elapsed_seconds <= 0:
            return None
        return self.elapsed_seconds / self.completed

    def eta_seconds(self) -> float | None:
        rate = self.per_still_seconds
        if rate is None or self.done:
            return None
        return rate * max(0, self.total - self.finished)

    def age_seconds(self, now: datetime | None = None) -> float | None:
        """How long since the mod last said anything.

        A number that stops moving is the signal that matters: the game can
        sit for minutes generating chunks, but a status file that has not
        changed in a very long time means the run is wedged, not busy.
        """
        if self.updated_at is None:
            return None
        now = now or datetime.now(timezone.utc)
        return (now - self.updated_at).total_seconds()


def read_progress(state_dir: Path) -> Progress | None:
    """Parse the mod's status file, or None when no run has written one.

    Returns None rather than raising on a torn read: the file is rewritten
    while we watch it, so catching a half-written copy is expected and the
    next poll a moment later will be whole.
    """
    path = Path(state_dir) / STATUS_NAME
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return None

    stamp = raw.get("updated_at")
    when = None
    if isinstance(stamp, (int, float)):
        # The mod writes epoch milliseconds.
        when = datetime.fromtimestamp(stamp / 1000, tz=timezone.utc)

    return Progress(
        total=int(raw.get("total", 0)),
        completed=int(raw.get("completed", 0)),
        skipped=int(raw.get("skipped", 0)),
        failed=int(raw.get("failed", 0)),
        current=raw.get("current") or None,
        elapsed_seconds=float(raw.get("elapsed_ms", 0)) / 1000,
        done=bool(raw.get("done", False)),
        updated_at=when,
    )


# --------------------------------------------------------------------- jobs


@dataclass(frozen=True)
class JobView:
    """The job either side of the one in flight, for a sense of movement."""

    current: dict | None = None
    previous: dict | None = None
    upcoming: list[dict] = field(default_factory=list)
    index: int | None = None
    total: int = 0


def read_jobs(state_dir: Path) -> list[dict]:
    path = Path(state_dir) / JOBS_NAME
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    jobs = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            jobs.append(json.loads(line))
        except ValueError:
            continue  # a torn final line while the batch is being written
    return jobs


def job_view(jobs: list[dict], current_id: str | None, ahead: int = 3) -> JobView:
    """Locate the running job in the batch, with its neighbours.

    The mod works the list in order, so the entry before the current one is
    the last picture taken and the entries after it are what is coming. That
    is cheaper and more exact than scanning the output tree for whichever
    file happens to have the newest timestamp.
    """
    if not jobs:
        return JobView()
    if current_id is None:
        return JobView(upcoming=jobs[:ahead], total=len(jobs))

    index = next(
        (i for i, job in enumerate(jobs) if job.get("id") == current_id), None
    )
    if index is None:
        return JobView(upcoming=jobs[:ahead], total=len(jobs))
    return JobView(
        current=jobs[index],
        previous=jobs[index - 1] if index else None,
        upcoming=jobs[index + 1 : index + 1 + ahead],
        index=index,
        total=len(jobs),
    )


def job_image(job: dict | None, library_root: Path, staging: Path, suffix: str):
    """Where a job's picture lives, whichever stage it has reached.

    Captures land in staging as lossless PNG and only become library images
    when the batch converts them at the end, so a run in flight has its
    output in one place and a finished one in the other.
    """
    if not job or not job.get("output"):
        return None
    output = Path(job["output"])
    png = Path(staging) / output
    if png.exists():
        return png
    final = Path(library_root) / output.with_suffix(f".{suffix}")
    return final if final.exists() else None


# ------------------------------------------------------------------- worlds


@dataclass(frozen=True)
class WorldRow:
    name: str
    seed: int | None = None
    camera: tuple | None = None
    is_selected: bool = False
    is_variant: bool = False
    error: str | None = None


def world_rows(worlds, read_seed, read_camera, selected: str | None) -> list[WorldRow]:
    """Describe each save, tolerating ones that cannot be read.

    A world that has never been entered has no player position, which is a
    fact worth showing rather than an error worth crashing on — it is exactly
    the world you still need to open and frame.
    """
    rows = []
    for world in worlds:
        name = world.name
        seed = camera = None
        error = None
        try:
            seed = read_seed(world)
        except Exception as problem:  # noqa: BLE001 - a bad save must not stop the list
            error = str(problem)
        try:
            camera = read_camera(world).as_tuple()
        except Exception as problem:  # noqa: BLE001
            error = error or str(problem)
        rows.append(
            WorldRow(
                name=name,
                seed=seed,
                camera=camera,
                is_selected=name == selected,
                # Snow variants are derived copies, not somewhere to render from.
                is_variant=name.endswith("-snow"),
                error=error,
            )
        )
    return rows


# ----------------------------------------------------------------- commands


@dataclass(frozen=True)
class OptionSpec:
    flags: tuple[str, ...]
    help: str = ""
    kind: str = "flag"  # flag | value | choice
    choices: tuple[str, ...] = ()
    default: object = None
    positional: bool = False

    @property
    def name(self) -> str:
        return self.flags[-1].lstrip("-").replace("-", "_")


@dataclass(frozen=True)
class CommandSpec:
    name: str
    help: str = ""
    options: tuple[OptionSpec, ...] = ()


def command_specs(parser) -> list[CommandSpec]:
    """Enumerate the CLI from its own parser, including nested groups.

    `mcwall world render` is one command as far as the TUI is concerned, so
    the tree is flattened to its leaves. Reading argparse rather than
    restating the commands means the TUI gains one the moment the CLI does,
    and can never offer one that no longer exists.
    """
    specs: list[CommandSpec] = []
    _walk(parser, (), specs)
    return sorted(specs, key=lambda s: s.name)


def _subparsers_action(parser):
    for action in parser._actions:
        if getattr(action, "choices", None) and hasattr(action, "add_parser"):
            return action
    return None


def _walk(parser, prefix: tuple[str, ...], specs: list[CommandSpec]) -> None:
    group = _subparsers_action(parser)
    if group is None:
        return
    summaries = {
        action.dest: (action.help or "")
        for action in getattr(group, "_choices_actions", [])
    }
    for name, sub in group.choices.items():
        path = prefix + (name,)
        if _subparsers_action(sub) is not None:
            _walk(sub, path, specs)
            continue
        specs.append(
            CommandSpec(
                name=" ".join(path),
                help=summaries.get(name) or sub.description or "",
                options=tuple(_options(sub)),
            )
        )


def _options(parser) -> list[OptionSpec]:
    found = []
    for action in parser._actions:
        if action.dest == "help":
            continue
        if not action.option_strings:
            # A positional, which the form offers as an ordinary value.
            found.append(OptionSpec(
                flags=(action.dest,), help=action.help or "",
                kind="value", positional=True,
            ))
            continue
        if action.choices:
            kind = "choice"
        elif action.nargs == 0 or isinstance(action.const, bool):
            kind = "flag"
        else:
            kind = "value"
        found.append(OptionSpec(
            flags=tuple(action.option_strings),
            help=action.help or "",
            kind=kind,
            choices=tuple(str(c) for c in (action.choices or ())),
            default=action.default,
        ))
    return found


def build_argv(spec: CommandSpec, values: dict) -> list[str]:
    """Turn a filled-in form into the argv the CLI would have been given.

    Positionals come first and unset ones are simply absent, which is why
    every positional in this CLI is optional. Anything left at its default is
    omitted, so the command shown is the short one you would have typed.
    """
    argv = spec.name.split()
    for option in spec.options:
        if not option.positional:
            continue
        value = values.get(option.flags[-1])
        if value not in (None, "", False):
            argv.append(str(value))
    for option in spec.options:
        if option.positional:
            continue
        flag = option.flags[-1]
        value = values.get(flag)
        if value is None or value is False or value == "":
            continue
        argv.append(flag) if value is True else argv.extend([flag, str(value)])
    return argv
