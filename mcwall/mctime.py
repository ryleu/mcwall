"""Vanilla Minecraft sky geometry, and the map from real time to world tick.

The sun's screen position is driven entirely by ``DimensionType.timeOfDay``:

    double d = Mth.frac(dayTime / 24000.0 - 0.25);
    double e = 0.5 - Math.cos(d * Math.PI) / 2.0;
    return (float)(d * 2.0 + e) / 3.0;

The sky renderer then rotates the celestial bodies about the X axis by
``timeOfDay * 360`` degrees, on a great circle through the zenith. So the sun's
elevation above the horizon is ``90 - |angle|`` with the angle wrapped to
(-180, 180].

This file is pure maths with no game and no I/O, so every claim in it is
unit-testable. ``tests/test_mctime.py`` restates the formula above
independently and checks this implementation against it; the source it was
read from is reachable with ``./gradlew genSources`` and
``scripts/unpack_sources.py``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TICKS_PER_DAY = 24000

# Elevations we anchor the real->tick mapping on. 0 deg is the visible horizon
# crossing; -6 deg is civil twilight, which is where the sky colour actually
# turns over.
HORIZON_DEG = 0.0
CIVIL_TWILIGHT_DEG = -6.0


def celestial_angle(tick: float) -> float:
    """Vanilla ``timeOfDay``: fraction of a full rotation, 0.0 == solar noon."""
    d = (tick / TICKS_PER_DAY - 0.25) % 1.0
    e = 0.5 - math.cos(d * math.pi) / 2.0
    return (d * 2.0 + e) / 3.0


def sun_elevation_deg(tick: float) -> float:
    """Degrees above the horizon, +90 at solar noon and -90 at midnight."""
    angle = celestial_angle(tick) * 360.0
    angle = (angle + 180.0) % 360.0 - 180.0
    return 90.0 - abs(angle)


def tick_at_elevation(elevation_deg: float, rising: bool) -> float:
    """Invert :func:`sun_elevation_deg` on one side of the day.

    ``rising`` picks the pre-noon branch (tick 18000..30000 modulo a day);
    otherwise the post-noon branch (tick 6000..18000). The function is strictly
    monotonic on each branch, so a bisection is exact to float precision.
    """
    if not -90.0 <= elevation_deg <= 90.0:
        raise ValueError(f"elevation {elevation_deg} out of range")

    # Branch bounds, chosen so elevation runs monotonically between them.
    lo, hi = (18000.0, 30000.0) if rising else (6000.0, 18000.0)

    def f(t: float) -> float:
        return sun_elevation_deg(t)

    # Rising branch climbs -90 -> +90; setting branch falls +90 -> -90.
    for _ in range(200):
        mid = (lo + hi) / 2.0
        value = f(mid)
        if (value < elevation_deg) == rising:
            lo = mid
        else:
            hi = mid
    return ((lo + hi) / 2.0) % TICKS_PER_DAY


@dataclass(frozen=True)
class SkyAnchors:
    """Vanilla ticks for the sun elevations we pin real time to."""

    sunrise: float
    civil_dawn: float
    solar_noon: float
    sunset: float
    civil_dusk: float
    solar_midnight: float

    @classmethod
    def vanilla(cls) -> "SkyAnchors":
        return cls(
            sunrise=tick_at_elevation(HORIZON_DEG, rising=True),
            civil_dawn=tick_at_elevation(CIVIL_TWILIGHT_DEG, rising=True),
            solar_noon=6000.0,
            sunset=tick_at_elevation(HORIZON_DEG, rising=False),
            civil_dusk=tick_at_elevation(CIVIL_TWILIGHT_DEG, rising=False),
            solar_midnight=18000.0,
        )


def tick_delta(a: float, b: float) -> float:
    """Signed shortest distance from tick ``a`` to tick ``b``, in (-12000, 12000]."""
    return (b - a + TICKS_PER_DAY / 2) % TICKS_PER_DAY - TICKS_PER_DAY / 2


def tick_distance(a: float, b: float) -> float:
    """Unsigned distance between two ticks on the day circle."""
    return abs(tick_delta(a, b))


def interpolate_circular(
    fraction: float, start_tick: float, end_tick: float
) -> float:
    """Walk ``fraction`` of the way from ``start_tick`` to ``end_tick`` forwards.

    Always travels in the increasing-tick direction, which is the direction the
    sun actually moves, so a segment that wraps past 24000 is handled correctly.
    """
    span = (end_tick - start_tick) % TICKS_PER_DAY
    if span == 0:
        span = TICKS_PER_DAY
    return (start_tick + fraction * span) % TICKS_PER_DAY
