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


def read_seed(world: Path) -> int | None:
    """The world's generation seed, from ``Data.WorldGenSettings.seed``."""
    import nbtlib

    level = world / "level.dat"
    if not level.exists():
        return None
    settings = nbtlib.load(level)["Data"].get("WorldGenSettings")
    if settings is None or "seed" not in settings:
        return None
    return int(settings["seed"])


def rename_level(world: Path, name: str) -> None:
    """Set a save's LevelName tag to match its directory.

    The mod identifies the loaded world by `getWorldData().getLevelName()`,
    which reads this tag rather than the directory. A copied save would keep
    the original's name and never match the job asking for it, so the batch
    would switch worlds forever.
    """
    import nbtlib

    level = world / "level.dat"
    with nbtlib.load(level) as handle:
        handle["Data"]["LevelName"] = nbtlib.String(name)


def derive_world(source: Path, target: Path) -> Path:
    """Copy a save, so the copy generates identical terrain.

    The winter grid renders in a snow-covered variant. Generating it fresh
    would only match the base world if both used the same seed, which stops
    being true the moment a world is created by hand — and the mismatch is
    invisible until a quarter of the library shows a different landscape.
    Copying sidesteps the seed entirely, and carries over anything built.
    """
    import shutil

    if target.exists():
        return target
    shutil.copytree(
        source, target, ignore=shutil.ignore_patterns("session.lock", "*.lock")
    )
    rename_level(target, target.name)
    log.info("derived %s from %s", target.name, source.name)
    return target


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
