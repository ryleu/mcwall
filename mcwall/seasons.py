"""Astronomical seasons, split into the 12 Serene Seasons sub-seasons.

Equinox and solstice instants come from Meeus, *Astronomical Algorithms*,
chapter 27 (accurate to well under a minute for 1000-3000 CE). That is far
tighter than we need: a sub-season boundary only has to land on the right day.

Computing them rather than tabulating them keeps the display path offline and
correct in any year.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

# Meeus 27.1: mean JDE of each event, argument Y = (year - 2000) / 1000.
_MEAN_TERMS = {
    "march_equinox": (2451623.80984, 365242.37404, 0.05169, -0.00411, -0.00057),
    "june_solstice": (2451716.56767, 365241.62603, 0.00325, 0.00888, -0.00030),
    "september_equinox": (2451810.21715, 365242.01767, -0.11575, 0.00337, 0.00078),
    "december_solstice": (2451900.05952, 365242.74049, -0.06223, 0.00823, 0.00032),
}

# Meeus table 27.C: periodic corrections, (A, B degrees, C degrees).
_PERIODIC = [
    (485, 324.96, 1934.136), (203, 337.23, 32964.467), (199, 342.08, 20.186),
    (182, 27.85, 445267.112), (156, 73.14, 45036.886), (136, 171.52, 22518.443),
    (77, 222.54, 65928.934), (74, 296.72, 3034.906), (70, 243.58, 9037.513),
    (58, 119.81, 33718.147), (52, 297.17, 150.678), (50, 21.02, 2281.226),
    (45, 247.54, 29929.562), (44, 325.15, 31555.956), (29, 60.93, 4443.417),
    (18, 155.12, 67555.328), (17, 288.79, 4562.452), (16, 198.04, 62894.029),
    (14, 199.76, 31436.921), (12, 95.39, 14577.848), (12, 287.11, 31931.756),
    (12, 320.81, 34777.259), (9, 227.73, 1222.114), (8, 15.45, 16859.074),
]

_EVENT_ORDER = (
    "march_equinox",
    "june_solstice",
    "september_equinox",
    "december_solstice",
)


class Season(str, Enum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class Phase(str, Enum):
    EARLY = "early"
    MID = "mid"
    LATE = "late"


@dataclass(frozen=True, order=True)
class SubSeason:
    """One of the 12 grid cells on the season axis."""

    season: Season
    phase: Phase

    @property
    def key(self) -> str:
        """Library path component, e.g. ``early_spring``."""
        return f"{self.phase.value}_{self.season.value}"

    @property
    def serene_name(self) -> str:
        """Serene Seasons' own enum name, e.g. ``EARLY_SPRING``."""
        return self.key.upper()

    @property
    def index(self) -> int:
        """0-11, in cycle order starting at early spring."""
        seasons = list(Season)
        phases = list(Phase)
        return seasons.index(self.season) * 3 + phases.index(self.phase)

    @classmethod
    def from_key(cls, key: str) -> "SubSeason":
        phase, season = key.split("_", 1)
        return cls(Season(season), Phase(phase))

    @classmethod
    def all(cls) -> list["SubSeason"]:
        return [cls(s, p) for s in Season for p in Phase]

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return self.key


def _jde_to_datetime(jde: float) -> datetime:
    """Julian Ephemeris Day -> UTC datetime (ignoring delta-T, ~70 s)."""
    z = math.floor(jde + 0.5)
    f = (jde + 0.5) - z
    if z < 2299161:
        a = z
    else:
        alpha = math.floor((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - math.floor(alpha / 4)
    b = a + 1524
    c = math.floor((b - 122.1) / 365.25)
    d = math.floor(365.25 * c)
    e = math.floor((b - d) / 30.6001)
    day = b - d - math.floor(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    whole = int(day)
    seconds = round((day - whole) * 86400)
    return datetime(int(year), int(month), whole, tzinfo=timezone.utc) + timedelta(
        seconds=seconds
    )


def solar_event(year: int, event: str) -> datetime:
    """UTC instant of an equinox or solstice. ``event`` is a key of _MEAN_TERMS."""
    a, b, c, d, e = _MEAN_TERMS[event]
    y = (year - 2000) / 1000.0
    jde0 = a + b * y + c * y**2 + d * y**3 + e * y**4

    t = (jde0 - 2451545.0) / 36525.0
    w = math.radians(35999.373 * t - 2.47)
    delta_lambda = 1 + 0.0334 * math.cos(w) + 0.0007 * math.cos(2 * w)
    s = sum(amp * math.cos(math.radians(bb + cc * t)) for amp, bb, cc in _PERIODIC)
    return _jde_to_datetime(jde0 + (0.00001 * s) / delta_lambda)


def _season_boundaries(year: int) -> list[tuple[datetime, str]]:
    """The six events bracketing ``year``, so any instant in it falls inside one."""
    events: list[tuple[datetime, str]] = []
    for y in (year - 1, year, year + 1):
        for name in _EVENT_ORDER:
            events.append((solar_event(y, name), name))
    events.sort()
    return events


# Which season each interval opens, by the event that starts it, per hemisphere.
_NORTHERN = {
    "march_equinox": Season.SPRING,
    "june_solstice": Season.SUMMER,
    "september_equinox": Season.AUTUMN,
    "december_solstice": Season.WINTER,
}
_SOUTHERN = {
    "march_equinox": Season.AUTUMN,
    "june_solstice": Season.WINTER,
    "september_equinox": Season.SPRING,
    "december_solstice": Season.SUMMER,
}


def sub_season_at(moment: datetime, latitude: float) -> SubSeason:
    """Which of the 12 sub-seasons ``moment`` falls in at this latitude.

    Each astronomical season (one solar event to the next) is split into equal
    thirds. The southern hemisphere is shifted six months, which falls out of
    using a different event-to-season table rather than offsetting dates.
    """
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    moment = moment.astimezone(timezone.utc)

    events = _season_boundaries(moment.year)
    for (start, name), (end, _) in zip(events, events[1:]):
        if start <= moment < end:
            break
    else:  # pragma: no cover - only reachable outside the +-1 year window
        raise ValueError(f"no season interval contains {moment!r}")

    table = _NORTHERN if latitude >= 0 else _SOUTHERN
    season = table[name]
    fraction = (moment - start) / (end - start)
    phase = list(Phase)[min(2, int(fraction * 3))]
    return SubSeason(season, phase)
