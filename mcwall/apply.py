"""Hand a chosen asset to the wallpaper daemon.

The whole display-time cost is here: one state file read, and a daemon call
only when the asset actually changes. Nothing in this module ever launches the
game or blocks on the network.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import STATE_DIR, DisplayConfig
from .select import Choice

log = logging.getLogger(__name__)

LAST_APPLIED = STATE_DIR / "applied.json"

# nixpkgs renamed swww to awww; both CLIs take the same arguments. Try the
# names in order and use whichever is installed.
STILL_DAEMONS = ("swww", "awww")


class NoWallpaperDaemon(Exception):
    pass


@dataclass(frozen=True)
class Applied:
    path: str
    kind: str
    cell: dict


def _daemon() -> tuple[str, str]:
    for name in STILL_DAEMONS:
        binary = shutil.which(name)
        if binary:
            return binary, shutil.which(f"{name}-daemon") or f"{binary}-daemon"
    raise NoWallpaperDaemon(
        "no wallpaper daemon found; install one of: " + ", ".join(STILL_DAEMONS)
    )


def read_last() -> Applied | None:
    try:
        data = json.loads(LAST_APPLIED.read_text())
        return Applied(data["path"], data["kind"], data["cell"])
    except (OSError, ValueError, KeyError):
        return None


def write_last(choice: Choice) -> None:
    LAST_APPLIED.parent.mkdir(parents=True, exist_ok=True)
    temporary = LAST_APPLIED.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(
            {
                "path": str(choice.path),
                "kind": choice.kind,
                "cell": choice.cell.as_dict(),
            }
        )
    )
    temporary.replace(LAST_APPLIED)


def _send(command: list[str], daemon: str, timeout: float = 10.0) -> None:
    """Run a daemon command, starting the daemon once if it is not up.

    There is no liveness probe that works across both CLIs — swww has a
    `query` subcommand, awww does not — so the command itself is the probe:
    try it, and if it fails, start the daemon and retry until it takes.
    """
    attempt = subprocess.run(command, capture_output=True, text=True)
    if attempt.returncode == 0:
        return

    log.info("starting %s", daemon)
    subprocess.Popen(
        [daemon], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(0.2)
        attempt = subprocess.run(command, capture_output=True, text=True)
        if attempt.returncode == 0:
            return
    raise NoWallpaperDaemon(
        f"{' '.join(command)} failed even after starting {daemon}: "
        f"{attempt.stderr.strip() or attempt.stdout.strip()}"
    )


def stop_loop() -> None:
    """Drop any running mpvpaper, e.g. when the machine goes onto battery."""
    subprocess.run(["pkill", "-f", "mpvpaper"], check=False)


def apply(choice: Choice, display: DisplayConfig, force: bool = False) -> bool:
    """Show ``choice``. Returns True if the wallpaper actually changed."""
    previous = read_last()
    if not force and previous is not None and previous.path == str(choice.path):
        return False

    if choice.kind == "loop":
        _apply_loop(choice)
    else:
        _apply_still(choice, display)

    write_last(choice)
    return True


def _apply_still(choice: Choice, display: DisplayConfig) -> None:
    binary, daemon = _daemon()
    stop_loop()
    _send(
        [
            binary, "img",
            "--transition-type", "fade",
            "--transition-duration", str(display.fade_seconds),
            "--transition-fps", "60",
            str(choice.path),
        ],
        daemon,
    )


def _apply_loop(choice: Choice) -> None:
    if not shutil.which("mpvpaper"):
        raise NoWallpaperDaemon("mpvpaper is not installed")
    stop_loop()
    subprocess.Popen(
        [
            "mpvpaper", "-o",
            "no-audio loop hwdec=auto-safe panscan=1.0 --no-osc --really-quiet",
            "-p",  # pause when the wallpaper is fully covered
            "ALL",
            str(choice.path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
