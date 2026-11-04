"""The library's three axes: weather, time slot, sub-season.

Time slots are spent where the sky changes fastest. Two 3,000-tick twilight
windows sit centred on the vanilla horizon crossings (not on ticks 0 and
12000), and day and night share the rest. The window list lives in config so
the twilight probe can rewrite it without a code change.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .mctime import TICKS_PER_DAY, tick_distance


class Weather(str, Enum):
    CLEAR = "clear"
    RAIN = "rain"
    THUNDER = "thunder"


# Degrade to the next entry when an exact cell is missing.
WEATHER_FALLBACK = {
    Weather.THUNDER: Weather.RAIN,
    Weather.RAIN: Weather.CLEAR,
    Weather.CLEAR: None,
}

# How dramatic each sky is, so a span of observations can be reduced to the
# most notable one. Deliberately the reverse of WEATHER_FALLBACK's chain.
_SEVERITY = {Weather.CLEAR: 0, Weather.RAIN: 1, Weather.THUNDER: 2}


def severity(weather: Weather) -> int:
    """Rank a sky, worst highest."""
    return _SEVERITY[Weather(weather)]


@dataclass(frozen=True)
class SlotWindow:
    """A contiguous arc of the vanilla day holding ``slots`` evenly spaced slots."""

    name: str
    start: int
    end: int
    slots: int

    @property
    def span(self) -> int:
        """Tick width, handling a window that wraps past 24000."""
        return (self.end - self.start) % TICKS_PER_DAY or TICKS_PER_DAY

    @property
    def spacing(self) -> float:
        return self.span / self.slots

    def ticks(self) -> list[int]:
        return [
            int(round(self.start + i * self.spacing)) % TICKS_PER_DAY
            for i in range(self.slots)
        ]


# Centred on the crossings solved in mctime: sunrise 23215, sunset 12785.
DEFAULT_WINDOWS = (
    SlotWindow("sunrise", 21700, 700, 12),
    SlotWindow("day", 700, 11300, 12),
    SlotWindow("sunset", 11300, 14300, 12),
    SlotWindow("night", 14300, 21700, 12),
)


def slot_ticks(windows=DEFAULT_WINDOWS) -> list[int]:
    """All slot ticks, sorted and de-duplicated."""
    ticks: list[int] = []
    for window in windows:
        ticks.extend(window.ticks())
    unique = sorted(set(ticks))
    if len(unique) != len(ticks):
        raise ValueError(f"windows produce duplicate slots: {ticks}")
    return unique


def nearest_slot(tick: float, ticks: list[int]) -> int:
    """The slot tick closest to ``tick`` on the day circle.

    Ties break towards the lower tick, so the function is deterministic.
    """
    if not ticks:
        raise ValueError("no slots configured")
    return min(ticks, key=lambda slot: (tick_distance(tick, slot), slot))


# WMO weather codes, as served by Open-Meteo, folded onto the three states the
# library has. Fog (45-48) renders as clear; snow (71-77, 85-86) renders as
# rain, which Serene Seasons turns into snowfall in winter sub-seasons.
def weather_from_wmo(code: int) -> Weather:
    if 95 <= code <= 99:
        return Weather.THUNDER
    if 51 <= code <= 67 or 71 <= code <= 77 or 80 <= code <= 86:
        return Weather.RAIN
    if 0 <= code <= 48:
        return Weather.CLEAR
    raise ValueError(f"unknown WMO weather code {code}")
