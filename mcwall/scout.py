"""Render candidate camera spots and lay them out as a contact sheet.

The camera is the one thing in this system no amount of maths can pick: it has
to be looked at. ``mcwall scout`` renders the same moment from several
positions, tiles them, and leaves the choice to a person.

The same machinery builds the per-sub-season contact sheets the phase 5 review
needs, so the tiling lives here rather than in the CLI.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .grid import Weather
from .library import GridCell
from .seasons import Phase, Season, SubSeason


@dataclass(frozen=True)
class Candidate:
    name: str
    x: float
    y: float
    z: float
    yaw: float
    pitch: float

    def as_camera(self) -> dict:
        return {"x": self.x, "y": self.y, "z": self.z, "yaw": self.yaw, "pitch": self.pitch}


# Heights above the ground at the camera spot, not absolute y. Complementary's
# distance fog washes out anything far above the canopy, so the useful range
# sits near the treetops: +4 stands among the trees, +70 is already pure haze.
DEFAULT_HEIGHT_OFFSETS = (4.0, 12.0, 30.0, 70.0)


def default_candidates(surface: float = 68.0) -> list[Candidate]:
    """A spread of heights and headings at the camera spot.

    ``surface`` is the ground height where the camera will stand, read from the
    last run's ``located.json``. The four headings catch whatever is scenic.
    """
    candidates = []
    for offset in DEFAULT_HEIGHT_OFFSETS:
        altitude = surface + offset
        for yaw, heading in ((-90.0, "east"), (0.0, "south"), (90.0, "west"), (180.0, "north")):
            candidates.append(
                Candidate(
                    name=f"h{int(offset):02d}-{heading}",
                    x=0.5,
                    y=altitude,
                    z=0.5,
                    yaw=yaw,
                    pitch=-12.0,
                )
            )
    return candidates


def scout_jobs(
    config: Config,
    candidates: list[Candidate],
    tick: int,
    sub_season: SubSeason,
    weather: Weather,
) -> list[dict]:
    """Job manifest entries for a camera sweep, one per candidate."""
    return [
        {
            "id": f"scout/{candidate.name}",
            "sub_season": sub_season.serene_name.lower(),
            "world": config.render.world,
            "tick": tick,
            "weather": weather.value,
            "output": f"scout/{candidate.name}.png",
            "camera": candidate.as_camera(),
        }
        for candidate in candidates
    ]


def probe_jobs(
    config: Config,
    step: int,
    sub_season: SubSeason = SubSeason(Season.SUMMER, Phase.MID),
    weather: Weather = Weather.CLEAR,
) -> list[dict]:
    """A twilight probe: the whole day sampled every ``step`` ticks.

    Phase 5 feeds these frames to :func:`even_change_slots` to place the 48
    slots where the sky actually changes fastest under this shaderpack, rather
    than where the plan guessed.
    """
    return [
        {
            "id": f"probe/{tick}",
            "sub_season": sub_season.serene_name.lower(),
            "world": config.render.world,
            "tick": tick,
            "weather": weather.value,
            "output": f"probe/{tick:05d}.png",
        }
        for tick in range(0, 24000, step)
    ]


def contact_sheet(images: list[Path], target: Path, columns: int = 4, width: int = 480) -> Path:
    """Tile images into one sheet with ffmpeg, labelled by file name."""
    if not images:
        raise ValueError("no images to tile")
    rows = math.ceil(len(images) / columns)
    target.parent.mkdir(parents=True, exist_ok=True)

    listing = target.with_suffix(".txt")
    listing.write_text("".join(f"file '{image}'\n" for image in images))

    command = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-r", "1", "-i", str(listing),
        "-vf", f"scale={width}:-1,tile={columns}x{rows}:margin=8:padding=4",
        "-frames:v", "1",
        str(target),
    ]
    subprocess.run(command, check=True)
    listing.unlink(missing_ok=True)
    return target


def sub_season_sheets(library, out_dir: Path, weather: Weather = Weather.CLEAR) -> list[Path]:
    """One contact sheet per sub-season, for the phase 5 look review.

    Each sheet is that sub-season's whole day in tick order, so a reviewer can
    see the twilight slots bunching where the sky moves and spot any frame that
    settled wrong — and, across sheets, whether the seasons actually differ.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    for sub_season in SubSeason.all():
        frames = []
        for tick in library.manifest.ticks:
            cell = GridCell(sub_season, weather, tick)
            if library.has_still(cell):
                frames.append(library.path_for(cell))
        if not frames:
            continue
        target = out_dir / f"{sub_season.key}-{weather.value}.png"
        sheets.append(contact_sheet(frames, target, columns=8, width=360))
    return sheets


def season_strip(library, out_dir: Path, tick: int, weather: Weather = Weather.CLEAR) -> Path | None:
    """All twelve sub-seasons at one moment of the day, side by side.

    The single most useful image for judging whether the season axis is worth
    twelve times the library: if these twelve frames look alike, the camera is
    in the wrong biome.
    """
    frames = []
    for sub_season in SubSeason.all():
        cell = GridCell(sub_season, weather, tick)
        if library.has_still(cell):
            frames.append(library.path_for(cell))
    if not frames:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    return contact_sheet(frames, out_dir / f"seasons-{tick}-{weather.value}.png",
                         columns=4, width=460)


def frame_difference(first: Path, second: Path) -> float:
    """Mean absolute per-pixel difference between two frames, 0-255.

    Used to place time slots: the sky changes fastest where this is largest.
    """
    from PIL import Image, ImageChops

    with Image.open(first) as a, Image.open(second) as b:
        a = a.convert("RGB")
        b = b.convert("RGB")
        if a.size != b.size:
            b = b.resize(a.size)
        difference = ImageChops.difference(a, b)
        histogram = difference.histogram()

    total = 0
    count = 0
    for channel in range(3):
        band = histogram[channel * 256 : (channel + 1) * 256]
        total += sum(value * level for level, value in enumerate(band))
        count += sum(band)
    return total / count if count else 0.0


def even_change_slots(frames: list[tuple[int, Path]], slots: int = 48) -> list[int]:
    """Place ``slots`` ticks so each step covers roughly equal visual change.

    ``frames`` is the probe output as (tick, path), in tick order. The
    cumulative difference curve is sampled at even intervals, which spends
    slots on twilight and saves them on the flat middle of the day and night.
    """
    if len(frames) < slots:
        raise ValueError(f"need at least {slots} probe frames, got {len(frames)}")

    ticks = [tick for tick, _ in frames]
    cumulative = [0.0]
    for (_, previous), (_, current) in zip(frames, frames[1:]):
        cumulative.append(cumulative[-1] + frame_difference(previous, current))
    # Close the loop back to the first frame so midnight is not a seam.
    cumulative.append(cumulative[-1] + frame_difference(frames[-1][1], frames[0][1]))

    total = cumulative[-1]
    if total <= 0:
        raise ValueError("probe frames are identical; nothing to place slots by")

    chosen: list[int] = []
    index = 0
    for slot in range(slots):
        target = total * slot / slots
        while index < len(cumulative) - 1 and cumulative[index + 1] < target:
            index += 1
        chosen.append(ticks[min(index, len(ticks) - 1)])

    unique = sorted(set(chosen))
    return unique


def write_slot_config(ticks: list[int], path: Path) -> Path:
    """Write the probe's slot list where the config loader will pick it up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Written by `mcwall probe`. Overrides [[grid.windows]].\n"
        "[grid]\n"
        f"slot_ticks = {json.dumps(ticks)}\n"
    )
    return path
