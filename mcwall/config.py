"""Configuration and on-disk locations.

Defaults live here; ``~/.config/mcwall/config.toml`` overrides them key by key.
Anything the tuning phase or the user is expected to change — camera spot, slot
windows, shaderpack, resolution — is config, never code.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .grid import DEFAULT_WINDOWS, SlotWindow


def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default)


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "mcwall"
STATE_DIR = _xdg("XDG_STATE_HOME", ".local/state") / "mcwall"
DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "mcwall"

CONFIG_PATH = Path(os.environ.get("MCWALL_CONFIG") or CONFIG_DIR / "config.toml")


@dataclass(frozen=True)
class Location:
    latitude: float | None = None
    longitude: float | None = None
    source: str = "ip"  # "ip" | "manual"


@dataclass(frozen=True)
class LibraryConfig:
    root: Path = DATA_DIR / "library"
    width: int = 2880
    height: int = 1800
    image_format: str = "webp"
    quality: int = 90
    # Dark frames use very little of the 8-bit range — a night sky spans about
    # 18 of 255 luma levels — so smooth gradients contour. gradfun interpolates
    # across the bands and dithers; the dither is what hides them, and a lossy
    # encode would strip it straight back out, so debanded output has to be
    # near-lossless. Costs roughly 8x the disk (0.4 GB -> 3.2 GB for the grid).
    deband: bool = True
    # cwebp -near_lossless: 0 most aggressive, 100 off.
    near_lossless: int = 60
    # The weather axis of the grid. Dropping "thunder" cuts 576 cells (a third
    # of the library, not half) and costs nothing in correctness: `select`
    # degrades thunder to rain through the normal fallback chain. Thunder does
    # visibly darken the sky, so it is kept by default.
    weathers: tuple[str, ...] = ("clear", "rain", "thunder")


@dataclass(frozen=True)
class RenderConfig:
    """Everything the producer needs. Nothing here may assume Hyprland."""

    prism_instance: str = "mc-wallpaper"
    prism_account: str = "StoleYourSushi"
    shaderpack: str = "ComplementaryUnbound_r5.9.3.zip"
    world: str = "mcwall-template"
    warmup_frames: int = 120
    # Camera spot in the template world: x y z yaw pitch.
    camera: tuple[float, float, float, float, float] = (0.5, 80.0, 0.5, -90.0, -10.0)
    render_distance: int = 16
    # Ticks the chunk counts must hold steady before a capture is allowed.
    terrain_stable_ticks: int = 40
    # Biomes to anchor the camera in. Serene Seasons recolours temperate
    # broadleaf foliage strongly and conifers barely at all, so a taiga camera
    # would make all twelve sub-seasons look alike. Empty disables the search
    # and uses `camera` as given.
    locate_biome: tuple[str, ...] = (
        "minecraft:forest",
        "minecraft:flower_forest",
        "minecraft:birch_forest",
        "minecraft:dark_forest",
        "minecraft:plains",
        "minecraft:meadow",
    )
    locate_radius: int = 6000
    # Winter sub-seasons render in a snow-covered copy of the template. Snow
    # layers only form on random block ticks, and the grid renders with
    # randomTickSpeed at 0, so the snow world gets one soak before any capture.
    snow_soak_ticks: int = 2400
    snow_random_tick_speed: int = 120
    snow_season: str = "mid_winter"
    job_timeout_seconds: int = 180
    allow_on_battery: bool = False


@dataclass(frozen=True)
class DisplayConfig:
    fade_seconds: float = 2.5
    weather_cache_seconds: int = 900
    # Below this, `apply` returns before doing any work, so the timer still
    # wakes but costs only a process spawn. How often it wakes is the
    # home-manager module's `interval`, not a setting here.
    low_battery_percent: int = 20
    allow_loops_on_ac: bool = True


@dataclass(frozen=True)
class Config:
    location: Location = field(default_factory=Location)
    library: LibraryConfig = field(default_factory=LibraryConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    windows: tuple[SlotWindow, ...] = DEFAULT_WINDOWS
    # Explicit slot list, written by the twilight probe. Overrides `windows`.
    slot_ticks: tuple[int, ...] | None = None

    @property
    def pack_id(self) -> str:
        """Library sub-directory for the current shaderpack build."""
        stem = Path(self.render.shaderpack).stem
        return stem.lower().replace(" ", "-").replace("_", "-")

    def ticks(self) -> list[int]:
        from .grid import slot_ticks

        if self.slot_ticks:
            return sorted(set(self.slot_ticks))
        return slot_ticks(self.windows)


def _merge(section: Any, data: dict[str, Any], renames: dict[str, str] | None = None):
    renames = renames or {}
    fields = {f for f in section.__dataclass_fields__}
    updates = {}
    for key, value in data.items():
        name = renames.get(key, key)
        if name not in fields:
            raise ValueError(f"unknown config key {key!r} in [{type(section).__name__}]")
        current = getattr(section, name)
        if isinstance(current, Path):
            value = Path(value).expanduser()
        elif isinstance(current, tuple) and isinstance(value, list):
            value = tuple(value)
        updates[name] = value
    return replace(section, **updates)


SLOTS_PATH = CONFIG_DIR / "slots.toml"


def load(path: Path | None = None) -> Config:
    """Read config, falling back to defaults for anything absent.

    ``slots.toml`` beside the main config is overlaid last. `mcwall probe`
    writes the measured slot list there rather than rewriting a hand-edited
    config file, and it wins over any `[[grid.windows]]`.
    """
    path = path or CONFIG_PATH
    config = Config()

    raw: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    if SLOTS_PATH.exists() and path == CONFIG_PATH:
        with SLOTS_PATH.open("rb") as handle:
            overlay = tomllib.load(handle)
        raw.setdefault("grid", {}).update(overlay.get("grid", {}))
    if not raw:
        return config

    if "location" in raw:
        config = replace(config, location=_merge(config.location, raw["location"]))
    if "library" in raw:
        config = replace(
            config,
            library=_merge(config.library, raw["library"], {"format": "image_format"}),
        )
    if "render" in raw:
        config = replace(config, render=_merge(config.render, raw["render"]))
    if "display" in raw:
        config = replace(config, display=_merge(config.display, raw["display"]))
    if "grid" in raw:
        grid = raw["grid"]
        if "slot_ticks" in grid:
            config = replace(config, slot_ticks=tuple(grid["slot_ticks"]))
        if "windows" in grid:
            config = replace(
                config,
                windows=tuple(SlotWindow(**w) for w in grid["windows"]),
            )
    return config


def update(section: str, values: dict[str, Any]) -> Path:
    """Merge values into one section of the user config, atomically."""
    import tomli_w

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    raw: dict[str, Any] = {}
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("rb") as handle:
            raw = tomllib.load(handle)
    raw.setdefault(section, {}).update(values)
    tmp = CONFIG_PATH.with_suffix(".toml.tmp")
    with tmp.open("wb") as handle:
        tomli_w.dump(raw, handle)
    tmp.replace(CONFIG_PATH)
    return CONFIG_PATH


def save_location(latitude: float, longitude: float, source: str) -> None:
    """Persist a detected or supplied location into the user config."""
    update("location", {"latitude": latitude, "longitude": longitude, "source": source})
