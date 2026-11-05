"""Map a real moment onto a library asset.

This is the whole display-time decision and it is a pure function: given a
moment, the observed conditions and a library index, it returns one asset. No
clock, no network, no game. ``tests/test_select.py`` drives it with fixtures.

Selection sits behind :class:`Selector` so mode 2 can swap in a lookup keyed by
real timestamp without touching :mod:`mcwall.apply`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from .grid import WEATHER_FALLBACK, Weather, nearest_slot
from .library import GridCell, Library
from .mctime import tick_distance
from .seasons import sub_season_at
from .sun import tick_for_moment


@dataclass(frozen=True)
class Conditions:
    """What the world is doing right now, as the display path observes it."""

    weather: Weather
    on_ac: bool = True


@dataclass(frozen=True)
class Choice:
    """The asset to show, plus why it was chosen."""

    path: Path
    cell: GridCell
    kind: str  # "still" | "loop"
    exact: bool
    reasons: tuple[str, ...] = ()
    wanted: GridCell | None = None


class NoAssetAvailable(Exception):
    """The library has nothing at all to show."""


class Selector(Protocol):
    def select(self, now: datetime, conditions: Conditions) -> Choice: ...


@dataclass
class Mode1Selector:
    """Mode 1: look up by sub-season, time slot and weather."""

    library: Library
    latitude: float
    longitude: float
    allow_loops: bool = True

    def target_cell(self, now: datetime, conditions: Conditions) -> GridCell:
        """The ideal grid cell for this moment, before any fallback."""
        tick = tick_for_moment(now, self.latitude, self.longitude)
        slot = nearest_slot(tick, self.library.manifest.ticks)
        return GridCell(
            sub_season=sub_season_at(now, self.latitude),
            weather=conditions.weather,
            tick=slot,
        )

    def select(self, now: datetime, conditions: Conditions) -> Choice:
        return self.resolve(
            self.target_cell(now, conditions), on_ac=conditions.on_ac
        )

    def resolve(self, wanted: GridCell, on_ac: bool = False) -> Choice:
        """Find the best available asset for a cell.

        Split out from `select` so a caller can ask for a cell it chose
        itself — forcing a season or a weather by hand — and still get the
        same substitution rules rather than a bare "not present".
        """
        want_loop = (
            self.allow_loops
            and on_ac
            and wanted.weather is not Weather.CLEAR
        )
        if want_loop:
            loop = self._nearest_loop(wanted)
            if loop is not None:
                cell, reasons = loop
                return Choice(
                    path=self.library.path_for(cell, loop=True),
                    cell=cell,
                    kind="loop",
                    exact=cell == wanted,
                    reasons=reasons,
                    wanted=wanted,
                )

        for cell, reasons in self._candidates(wanted):
            if self.library.has_still(cell):
                return Choice(
                    path=self.library.path_for(cell),
                    cell=cell,
                    kind="still",
                    exact=cell == wanted,
                    reasons=reasons,
                    wanted=wanted,
                )
        raise NoAssetAvailable(f"nothing in the library near {wanted.as_dict()}")

    def _candidates(self, wanted: GridCell):
        """Fallback order: exact, then weather, then the nearest time slot.

        Season is never substituted — a summer sky in December is more wrong
        than a clear sky during a shower.
        """
        yield wanted, ()

        degraded: list[tuple[GridCell, tuple[str, ...]]] = []
        reasons: tuple[str, ...] = ()
        current = wanted.weather
        while (weather := WEATHER_FALLBACK[current]) is not None:
            reasons = reasons + (f"weather {current.value}->{weather.value}",)
            degraded.append((GridCell(wanted.sub_season, weather, wanted.tick), reasons))
            current = weather
        yield from degraded

        # Then walk out in time, keeping the weather preference order.
        by_distance = sorted(
            self.library.manifest.ticks,
            key=lambda t: (tick_distance(wanted.tick, t), t),
        )
        for tick in by_distance:
            if tick == wanted.tick:
                continue
            note = (f"slot {wanted.tick}->{tick}",)
            yield GridCell(wanted.sub_season, wanted.weather, tick), note
            for cell, why in degraded:
                yield GridCell(cell.sub_season, cell.weather, tick), why + note

    def _nearest_loop(self, wanted: GridCell):
        if self.library.has_loop(wanted):
            return wanted, ()
        loop_ticks = self.library.manifest.loop_ticks
        if not loop_ticks:
            return None
        for tick in sorted(loop_ticks, key=lambda t: (tick_distance(wanted.tick, t), t)):
            cell = GridCell(wanted.sub_season, wanted.weather, tick)
            if self.library.has_loop(cell):
                return cell, (f"loop slot {wanted.tick}->{tick}",)
        return None
