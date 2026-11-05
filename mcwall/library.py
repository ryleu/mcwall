"""The library format — the only contract between producer and display path.

Layout::

    <root>/manifest.json
    <root>/<build-id>/<sub-season>/<weather>/<tick>.webp
    <root>/<build-id>/loops/<sub-season>/<weather>/<tick>.mp4

The build id names what produced the images — world, seed, shaderpack, and a
hash of everything else that changes what they look like — so two builds can
sit side by side without colliding. The display path treats it as an opaque
directory name and reads it out of the manifest.

Nothing in here knows about Hyprland, Prism or the local machine, so a mode 2
producer on another host can write the same shape and the display path will
read it unchanged.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from .grid import Weather
from .seasons import SubSeason

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MANIFEST_NAME = "manifest.json"


@dataclass(frozen=True)
class GridCell:
    """One addressable point in the library."""

    sub_season: SubSeason
    weather: Weather
    tick: int

    def relative_path(self, pack_id: str, suffix: str, loop: bool = False) -> Path:
        # An empty pack_id means the manifest sits in the build directory
        # itself, so the images are directly beneath it.
        parts = [pack_id] if pack_id else []
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
    camera: list[float] | None = None
    render_distance: int | None = None
    loop_ticks: list[int] = field(default_factory=list)
    notes: dict = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        config,
        settings_hash: str = "",
        pack_id: str | None = None,
        camera: tuple | None = None,
    ) -> "Manifest":
        return cls(
            pack_id=pack_id or config.pack_stem,
            pack_name=config.render.shaderpack,
            settings_hash=settings_hash,
            width=config.library.width,
            height=config.library.height,
            image_format=config.library.image_format,
            sub_seasons=[s.key for s in SubSeason.all()],
            weathers=[Weather(w).value for w in config.library.weathers],
            ticks=config.ticks(),
            camera=list(camera) if camera else None,
            render_distance=config.render.render_distance,
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

        # Fields get added to this dataclass as builds learn to record more
        # about themselves. Those additions are descriptive, not structural,
        # so a reader that predates them can still find every image — but
        # `cls(**data)` would reject them outright, and a display path that
        # dies on a field it does not need is the worse failure. Drop what we
        # do not recognise and say so once.
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            log.warning(
                "ignoring manifest fields this build does not know: %s "
                "(the library was written by a newer mcwall)",
                ", ".join(unknown),
            )
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass(frozen=True)
class BuildSummary:
    """One build directory, as the chooser sees it."""

    pack_id: str
    path: Path
    present: int
    total: int
    active: bool
    manifest: "Manifest | None" = None

    @property
    def complete(self) -> bool:
        return bool(self.total) and self.present == self.total

    @property
    def switchable(self) -> bool:
        """A build with no manifest cannot describe itself, so it cannot be
        made active — nothing would know the grid its images belong to."""
        return self.manifest is not None


def builds(root: Path, active: Path | None = None) -> list[BuildSummary]:
    """Every build sitting under ``root``, in name order.

    Reads each build's own manifest rather than the active one, so a library
    that is not currently on screen still reports its real size. ``active`` is
    the directory the display path is reading, whatever chose it.
    """
    root = Path(root)
    active = Path(active).resolve() if active else None

    found = []
    for path in sorted(p for p in root.iterdir() if p.is_dir()):
        if path.name.startswith("."):
            continue
        manifest = None
        try:
            manifest = Manifest.read(path)
        except (OSError, ValueError):
            pass
        suffix = f".{manifest.image_format}" if manifest else ".webp"
        total = len(manifest.cells()) if manifest else 0
        present = sum(1 for p in path.rglob(f"*{suffix}") if p.is_file())
        if not present and not manifest:
            continue  # not a build directory at all
        found.append(BuildSummary(
            pack_id=manifest.pack_id if manifest else path.name,
            path=path,
            present=present,
            total=total,
            active=active is not None and path.resolve() == active,
            manifest=manifest,
        ))
    return found


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
        # Set properly by `open`; a hand-built Library keeps the old layout.
        self.nested = True
        self.stills = stills if stills is not None else set()
        self.loops = loops if loops is not None else set()

    @property
    def suffix(self) -> str:
        return f".{self.manifest.image_format}"

    @classmethod
    def open(cls, root: Path) -> "Library":
        """Open the library whose manifest is in ``root``.

        Two layouts are possible and both are read the same way. A manifest at
        the top of the library tree describes a build in a subdirectory named
        after it; a manifest inside a build directory describes the images
        beside it. The second is what lets a config point straight at one
        library, wherever it lives.
        """
        root = Path(root)
        manifest = Manifest.read(root)
        library = cls(root, manifest)
        library.nested = (root / manifest.pack_id).is_dir()
        library.rescan()
        return library

    def rescan(self) -> None:
        self.stills = {c for c in self.manifest.cells() if self.path_for(c).exists()}
        self.loops = {
            c for c in self.manifest.cells() if self.path_for(c, loop=True).exists()
        }

    def path_for(self, cell: GridCell, loop: bool = False) -> Path:
        suffix = ".mp4" if loop else self.suffix
        prefix = self.manifest.pack_id if self.nested else ""
        return self.root / cell.relative_path(prefix, suffix, loop)

    def has_still(self, cell: GridCell) -> bool:
        return cell in self.stills

    def has_loop(self, cell: GridCell) -> bool:
        return cell in self.loops

    def missing_stills(self) -> list[GridCell]:
        return [c for c in self.manifest.cells() if c not in self.stills]
