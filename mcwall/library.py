"""The library format — the only contract between producer and display path.

Layout::

    <root>/manifest.json
    <root>/<pack-id>/<sub-season>/<weather>/<tick>.webp
    <root>/<pack-id>/loops/<sub-season>/<weather>/<tick>.mp4

Nothing in here knows about Hyprland, Prism or the local machine, so a mode 2
producer on another host can write the same shape and the display path will
read it unchanged.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .grid import Weather
from .seasons import SubSeason

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class GridCell:
    """One addressable point in the library."""

    sub_season: SubSeason
    weather: Weather
    tick: int

    def relative_path(self, pack_id: str, suffix: str, loop: bool = False) -> Path:
        parts = [pack_id]
        if loop:
            parts.append("loops")
        parts += [self.sub_season.key, self.weather.value, f"{self.tick}{suffix}"]
        return Path(*parts)

    def as_dict(self) -> dict:
        return {
            "sub_season": self.sub_season.key,
            "weather": self.weather.value,
            "tick": self.tick,
        }


@dataclass
class Manifest:
    pack_id: str
    pack_name: str
    settings_hash: str
    width: int
    height: int
    image_format: str
    sub_seasons: list[str]
    weathers: list[str]
    ticks: list[int]
    mode: int = 1
    schema: int = SCHEMA_VERSION
    rendered_at: str | None = None
    # Which world the images came from, so a library can be traced back to a
    # reproducible starting point.
    world: str | None = None
    seed: int | None = None
    loop_ticks: list[int] = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    @classmethod
    def from_config(cls, config, settings_hash: str = "") -> "Manifest":
        return cls(
            pack_id=config.pack_id,
            pack_name=config.render.shaderpack,
            settings_hash=settings_hash,
            width=config.library.width,
            height=config.library.height,
            image_format=config.library.image_format,
            sub_seasons=[s.key for s in SubSeason.all()],
            weathers=[Weather(w).value for w in config.library.weathers],
            ticks=config.ticks(),
        )

    def cells(self) -> list[GridCell]:
        return [
            GridCell(SubSeason.from_key(s), Weather(w), t)
            for s in self.sub_seasons
            for w in self.weathers
            for t in self.ticks
        ]

    def write(self, root: Path) -> Path:
        root.mkdir(parents=True, exist_ok=True)
        path = root / MANIFEST_NAME
        payload = asdict(self)
        payload["written_at"] = datetime.now(timezone.utc).isoformat()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(path)
        return path

    @classmethod
    def read(cls, root: Path) -> "Manifest":
        data = json.loads((root / MANIFEST_NAME).read_text())
        data.pop("written_at", None)
        if data.get("schema") != SCHEMA_VERSION:
            raise ValueError(f"unsupported library schema {data.get('schema')}")
        return cls(**data)


class Library:
    """Read-side view of a library directory.

    ``index`` is the set of cells actually present, so selection logic can be
    tested with an in-memory set and no files at all.
    """

    def __init__(
        self,
        root: Path,
        manifest: Manifest,
        stills: set[GridCell] | None = None,
        loops: set[GridCell] | None = None,
    ):
        self.root = Path(root)
        self.manifest = manifest
        self.stills = stills if stills is not None else set()
        self.loops = loops if loops is not None else set()

    @property
    def suffix(self) -> str:
        return f".{self.manifest.image_format}"

    @classmethod
    def open(cls, root: Path) -> "Library":
        manifest = Manifest.read(Path(root))
        library = cls(root, manifest)
        library.rescan()
        return library

    def rescan(self) -> None:
        self.stills = {c for c in self.manifest.cells() if self.path_for(c).exists()}
        self.loops = {
            c for c in self.manifest.cells() if self.path_for(c, loop=True).exists()
        }

    def path_for(self, cell: GridCell, loop: bool = False) -> Path:
        suffix = ".mp4" if loop else self.suffix
        return self.root / cell.relative_path(self.manifest.pack_id, suffix, loop)

    def has_still(self, cell: GridCell) -> bool:
        return cell in self.stills

    def has_loop(self, cell: GridCell) -> bool:
        return cell in self.loops

    def missing_stills(self) -> list[GridCell]:
        return [c for c in self.manifest.cells() if c not in self.stills]
