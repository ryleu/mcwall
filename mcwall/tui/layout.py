"""Turn state into lines. No curses, no I/O — just text and a style name.

Keeping the arrangement separate from the drawing means the screen can be
asserted against in a test, which is the only practical way to check a
terminal UI without a terminal.
"""

from __future__ import annotations

from dataclasses import dataclass

# Style names the drawing layer maps onto attributes.
PLAIN, TITLE, DIM, OK, WARN, ERR, SEL, BAR = (
    "plain", "title", "dim", "ok", "warn", "err", "sel", "bar",
)


@dataclass(frozen=True)
class Line:
    text: str
    style: str = PLAIN


def human_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(max(0, seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def progress_bar(fraction: float, width: int) -> str:
    """A bar that never lies about being complete.

    Rounding up would show a full bar with jobs still queued, so a fraction
    short of 1.0 always leaves at least one cell unfilled.
    """
    width = max(4, width)
    filled = int(fraction * width)
    if fraction < 1.0:
        filled = min(filled, width - 1)
    return "█" * filled + "░" * (width - filled)


def describe_job(job: dict | None) -> str:
    if not job:
        return "—"
    return job.get("id") or "—"


def dashboard(snapshot, width: int) -> list[Line]:
    """The headline view: what is being built, and how far along it is."""
    width = max(40, width)
    lines: list[Line] = []

    lines.append(Line("build", TITLE))
    lines.append(Line(f"  library   {snapshot.build_key or '—'}"))
    lines.append(Line(f"  world     {snapshot.world or '—'}"
                      f"{f'  seed {snapshot.seed}' if snapshot.seed is not None else ''}"))
    if snapshot.camera:
        x, y, z, yaw, pitch = snapshot.camera
        lines.append(Line(f"  camera    x={x:.1f} y={y:.1f} z={z:.1f} "
                          f"yaw={yaw:.0f} pitch={pitch:.0f}"))
    if snapshot.active_build and snapshot.active_build != snapshot.build_key:
        lines.append(Line(f"  on screen {snapshot.active_build}", DIM))
    lines.append(Line(""))

    progress = snapshot.progress
    lines.append(Line("render", TITLE))
    if progress is None:
        lines.append(Line("  no run has reported yet", DIM))
        lines.append(Line("  press r to start one, or c for the command list", DIM))
        return lines

    bar = progress_bar(progress.fraction, width - 24)
    lines.append(Line(f"  {bar} {progress.fraction * 100:5.1f}%", BAR))
    counted = f"  {progress.finished}/{progress.total} stills"
    if progress.skipped:
        counted += f"  ({progress.skipped} already present)"
    lines.append(Line(counted))

    pace = progress.per_still_seconds
    lines.append(Line(
        f"  {('%.1f s/still' % pace) if pace else 'pace —'}"
        f"   elapsed {human_duration(progress.elapsed_seconds)}"
        f"   eta {human_duration(progress.eta_seconds())}"
    ))

    if progress.failed:
        lines.append(Line(f"  {progress.failed} failed", ERR))

    age = progress.age_seconds()
    if progress.done:
        lines.append(Line("  batch reported DONE", OK))
    elif not snapshot.client_running:
        lines.append(Line("  no client is running — the batch stopped early", ERR))
    elif age is not None and age > 300:
        lines.append(Line(f"  nothing for {human_duration(age)} — possibly wedged", WARN))
    elif age is not None and age > 90:
        lines.append(Line(f"  quiet for {human_duration(age)} "
                          f"(chunk generation is slow)", DIM))

    lines.append(Line(""))
    lines.append(Line("jobs", TITLE))
    view = snapshot.jobs
    position = f"{view.index + 1}/{view.total}" if view.index is not None else "—"
    lines.append(Line(f"  now   {describe_job(view.current)}   [{position}]"))
    lines.append(Line(f"  last  {describe_job(view.previous)}", DIM))
    for upcoming in view.upcoming:
        lines.append(Line(f"  next  {describe_job(upcoming)}", DIM))
    if snapshot.last_image:
        lines.append(Line(f"  image {snapshot.last_image.name}", DIM))
    return lines


def worlds(rows, cursor: int) -> list[Line]:
    lines = [Line("worlds in the render instance", TITLE)]
    if not rows:
        lines.append(Line("  none — run setup, then edit to make one", DIM))
        return lines
    for index, row in enumerate(rows):
        marker = ">" if index == cursor else " "
        flag = "*" if row.is_selected else " "
        label = f"{marker}{flag} {row.name:<22}"
        if row.seed is not None:
            label += f" seed {row.seed:<22}"
        if row.camera:
            x, _, z, yaw, _ = row.camera
            label += f" x={x:.0f} z={z:.0f} yaw={yaw:.0f}"
        style = SEL if index == cursor else (DIM if row.is_variant else PLAIN)
        lines.append(Line(label, style))
        if row.error and index == cursor:
            lines.append(Line(f"     {row.error}", WARN))
    lines.append(Line(""))
    lines.append(Line("  * renders from this world   enter: select   "
                      "e: open it in the game", DIM))
    return lines


def commands(specs, cursor: int) -> list[Line]:
    lines = [Line("commands", TITLE)]
    for index, spec in enumerate(specs):
        marker = ">" if index == cursor else " "
        style = SEL if index == cursor else PLAIN
        lines.append(Line(f"{marker} {spec.name:<10} {spec.help}", style))
    lines.append(Line(""))
    lines.append(Line("  enter: set options and run", DIM))
    return lines


def command_form(spec, values: dict, cursor: int, argv: list[str]) -> list[Line]:
    lines = [Line(f"mcwall {spec.name}", TITLE)]
    if spec.help:
        lines.append(Line(f"  {spec.help}", DIM))
    lines.append(Line(""))
    if not spec.options:
        lines.append(Line("  no options", DIM))
    for index, option in enumerate(spec.options):
        marker = ">" if index == cursor else " "
        value = values.get(option.flags[-1])
        if option.kind == "flag":
            shown = "on" if value else "off"
        else:
            shown = "—" if value in (None, "") else str(value)
        style = SEL if index == cursor else PLAIN
        lines.append(Line(f"{marker} {option.flags[-1]:<18} {shown:<16} "
                          f"{option.help}", style))
    lines.append(Line(""))
    lines.append(Line(f"  $ mcwall {' '.join(argv[1:]) if len(argv) > 1 else argv[0]}",
                      OK))
    lines.append(Line("  space: toggle   enter: edit value   "
                      "x: run   esc: back", DIM))
    return lines


def log(entries, running: str | None, tail: int) -> list[Line]:
    title = f"output — running: mcwall {running}" if running else "output"
    lines = [Line(title, TITLE if not running else WARN)]
    if not entries:
        lines.append(Line("  nothing yet", DIM))
    for entry in list(entries)[-tail:]:
        style = PLAIN
        lowered = entry.lower()
        if "error" in lowered or "traceback" in lowered or "failed" in lowered:
            style = ERR
        elif "warning" in lowered:
            style = WARN
        lines.append(Line(f"  {entry}", style))
    return lines


def help_bar(view: str, busy: bool) -> str:
    keys = {
        "dashboard": "d dashboard  w worlds  c commands  o output  "
                     "r render  e edit  i image  x stop  q quit",
        "worlds": "d dashboard  w worlds  c commands  o output  "
                  "enter select  e open  q quit",
        "commands": "d dashboard  w worlds  c commands  o output  "
                    "enter open  q quit",
        "form": "space toggle  enter edit  x run  esc back",
        "output": "d dashboard  w worlds  c commands  o output  "
                  "x stop  i image  q quit",
    }
    bar = keys.get(view, keys["dashboard"])
    return bar + ("   [running]" if busy else "")
