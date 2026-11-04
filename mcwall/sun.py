"""Real-world sun times, and the anchored map from a real moment to a world tick.

The hard requirement from the plan: the sun must cross the horizon at the real
local times. We get that by pinning six real instants per day to the vanilla
ticks at the *same sun elevation*, then interpolating linearly between them.
Seasonal day length is absorbed because the real anchors move while the ticks
stay put.

Implemented on astral rather than Skyfield: astral is pure Python with no
ephemeris file, so the display path never downloads anything and never blocks.
``tests/test_sun.py`` cross-checks it against Skyfield when the network is up
(marked ``network``); the two agree to well under a minute.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta, timezone

from astral import Observer
from astral.sun import midnight, noon, time_at_elevation
from astral.sun import SunDirection

from .mctime import (
    CIVIL_TWILIGHT_DEG,
    SkyAnchors,
    TICKS_PER_DAY,
    interpolate_circular,
)


@dataclass(frozen=True)
class Anchor:
    """One real instant pinned to one vanilla tick."""

    when: datetime
    tick: float
    name: str


# The visible sunrise is the sun's upper limb touching the horizon: geometric
# elevation -0.833 deg, refraction plus the solar radius. astral's
# time_at_elevation applies a refraction correction of its own unless told not
# to, so asking it for -0.833 double-counts and lands about two minutes early.
# All four elevation anchors therefore pass with_refraction=False.
# tests/test_sun.py cross-checks the result against Skyfield/de421.
VISIBLE_HORIZON_DEG = -0.833


class PolarDay(Exception):
    """The sun does not reach a needed elevation on this date at this latitude."""


def _observer(latitude: float, longitude: float, elevation: float = 0.0) -> Observer:
    return Observer(latitude=latitude, longitude=longitude, elevation=elevation)


def _at(obs: Observer, elevation: float, day: Date, direction) -> datetime:
    """Time the sun's centre reaches a *geometric* elevation."""
    return time_at_elevation(
        obs, elevation, day, direction=direction, with_refraction=False
    )


def day_anchors(
    latitude: float, longitude: float, day: Date, sky: SkyAnchors | None = None
) -> list[Anchor]:
    """The six anchors for one local date, in increasing real time.

    Raises :class:`PolarDay` if the sun never reaches the horizon or civil
    twilight, which happens above the polar circles near the solstices.
    """
    sky = sky or SkyAnchors.vanilla()
    obs = _observer(latitude, longitude)

    try:
        anchors = [
            Anchor(midnight(obs, day), sky.solar_midnight, "solar_midnight"),
            Anchor(
                _at(obs, CIVIL_TWILIGHT_DEG, day, SunDirection.RISING),
                sky.civil_dawn,
                "civil_dawn",
            ),
            Anchor(
                _at(obs, VISIBLE_HORIZON_DEG, day, SunDirection.RISING),
                sky.sunrise,
                "sunrise",
            ),
            Anchor(noon(obs, day), sky.solar_noon, "solar_noon"),
            Anchor(
                _at(obs, VISIBLE_HORIZON_DEG, day, SunDirection.SETTING),
                sky.sunset,
                "sunset",
            ),
            Anchor(
                _at(obs, CIVIL_TWILIGHT_DEG, day, SunDirection.SETTING),
                sky.civil_dusk,
                "civil_dusk",
            ),
        ]
    except ValueError as exc:  # astral's "sun never reaches ..." errors
        raise PolarDay(str(exc)) from exc

    # Canonical name order, not time order: astral keys each event off the UTC
    # date, so for longitudes far from Greenwich the six instants of one UTC
    # date are not a single local solar day. Callers merge several dates and
    # sort by time.
    return [Anchor(a.when.astimezone(timezone.utc), a.tick, a.name) for a in anchors]


def _fallback_tick(moment: datetime, longitude: float) -> float:
    """Mean-solar-time mapping, used when there is no sunrise to anchor to.

    Local mean solar noon becomes tick 6000 and midnight 18000, with no warping.
    The sky is wrong by up to a couple of hours inside the polar circles, but
    the display path keeps working.
    """
    moment = moment.astimezone(timezone.utc)
    seconds = (
        moment.hour * 3600 + moment.minute * 60 + moment.second + longitude * 240.0
    )
    day_fraction = (seconds / 86400.0) % 1.0
    return (day_fraction * TICKS_PER_DAY + 18000.0) % TICKS_PER_DAY


def tick_for_moment(
    moment: datetime,
    latitude: float,
    longitude: float,
    sky: SkyAnchors | None = None,
) -> float:
    """Map a real instant onto the vanilla day, 0-24000.

    Pure given its arguments: no clock, no network, no game.
    """
    if moment.tzinfo is None:
        raise ValueError("moment must be timezone-aware")
    moment = moment.astimezone(timezone.utc)
    sky = sky or SkyAnchors.vanilla()

    # A UTC date's anchors can sit up to 12 hours either side of that date, so
    # take a five-day window and merge. Any day the sun misses an elevation on
    # simply contributes nothing.
    anchors: list[Anchor] = []
    base = moment.date()
    for offset in (-2, -1, 0, 1, 2):
        try:
            anchors.extend(
                day_anchors(latitude, longitude, base + timedelta(days=offset), sky)
            )
        except PolarDay:
            continue
    anchors.sort(key=lambda a: a.when)

    for left, right in zip(anchors, anchors[1:]):
        if left.when <= moment < right.when:
            # Consecutive anchors are at most a few thousand ticks apart. A
            # wider gap means the sorted list skipped an anchor, which would
            # make the interpolation run backwards around the day.
            span_ticks = (right.tick - left.tick) % TICKS_PER_DAY
            if span_ticks > 12000:
                break
            span = (right.when - left.when).total_seconds()
            fraction = ((moment - left.when).total_seconds() / span) if span else 0.0
            return interpolate_circular(fraction, left.tick, right.tick)

    return _fallback_tick(moment, longitude)
