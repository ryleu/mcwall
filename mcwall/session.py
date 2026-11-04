"""Set the shot by hand, then hand it to the CLI.

The camera is the one thing no amount of maths can choose: it has to be
looked at. So `mcwall edit` opens the render instance as an ordinary game —
fly to a spot, set the framing, tune the shaderpack — and on quit the camera
is read back out of the world's ``level.dat`` and written to config.

Minecraft saves the player's position and rotation on exit, so nothing has to
be captured live and the mod stays out of the way entirely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

STASHED_JOBS = "jobs.jsonl.idle"


@dataclass(frozen=True)
class Camera:
    x: float
    y: float
    z: float
    yaw: float
    pitch: float

    def as_tuple(self) -> tuple[float, float, float, float, float]:
        return (self.x, self.y, self.z, self.yaw, self.pitch)

    def __str__(self) -> str:
        return (
            f"x={self.x:.2f} y={self.y:.2f} z={self.z:.2f} "
            f"yaw={self.yaw:.1f} pitch={self.pitch:.1f}"
        )


class NoPlayerPosition(Exception):
    """The world has never been played in, so it has no saved camera."""


def saves_dir(game_dir: Path) -> Path:
    return game_dir / "saves"


def worlds(game_dir: Path) -> list[Path]:
    """Every save in the instance, most recently played first."""
    root = saves_dir(game_dir)
    if not root.is_dir():
        return []
    found = [p for p in root.iterdir() if (p / "level.dat").exists()]
    return sorted(found, key=lambda p: (p / "level.dat").stat().st_mtime, reverse=True)


def latest_world(game_dir: Path) -> Path | None:
    """The save touched most recently — what they just quit out of."""
    found = worlds(game_dir)
    return found[0] if found else None


def read_camera(world: Path) -> Camera:
    """Where the player was standing, and which way they were looking."""
    import nbtlib

    level = world / "level.dat"
    if not level.exists():
        raise NoPlayerPosition(f"{world.name} has no level.dat")

    data = nbtlib.load(level)["Data"]
    player = data.get("Player")
    if player is None or "Pos" not in player:
        raise NoPlayerPosition(
            f"{world.name} has no saved player position — open it once with "
            f"`mcwall edit` and quit from inside the world"
        )
    position = [float(v) for v in player["Pos"]]
    rotation = [float(v) for v in player["Rotation"]]
    return Camera(
        x=position[0],
        y=position[1],
        z=position[2],
        yaw=rotation[0],
        pitch=rotation[1],
    )


def stash_jobs(state: Path) -> bool:
    """Move the job manifest aside so the mod idles while a person plays.

    Without this, opening the instance by hand replays the last batch: the
    mod finds `jobs.jsonl`, starts capturing, and quits the game out from
    under whoever is setting up the shot.
    """
    jobs = state / "jobs.jsonl"
    if not jobs.exists():
        return False
    jobs.replace(state / STASHED_JOBS)
    return True


def restore_jobs(state: Path) -> None:
    """Put a stashed manifest back, so a resumed render still finds it."""
    stashed = state / STASHED_JOBS
    if stashed.exists():
        stashed.replace(state / "jobs.jsonl")
