import math

import pytest

from mcwall.mctime import (
    SkyAnchors,
    TICKS_PER_DAY,
    celestial_angle,
    interpolate_circular,
    sun_elevation_deg,
    tick_at_elevation,
    tick_delta,
    tick_distance,
)


def test_celestial_angle_matches_vanilla_formula():
    # Re-stated independently of the implementation, straight from
    # DimensionType.timeOfDay.
    for tick in range(0, TICKS_PER_DAY, 137):
        d = math.fmod(tick / 24000.0 - 0.25, 1.0)
        if d < 0:
            d += 1.0
        e = 0.5 - math.cos(d * math.pi) / 2.0
        assert celestial_angle(tick) == pytest.approx((d * 2.0 + e) / 3.0)


def test_noon_and_midnight_are_the_poles():
    assert celestial_angle(6000) == pytest.approx(0.0)
    assert sun_elevation_deg(6000) == pytest.approx(90.0)
    assert celestial_angle(18000) == pytest.approx(0.5)
    assert sun_elevation_deg(18000) == pytest.approx(-90.0)


def test_horizon_crossings_match_the_documented_ticks():
    sky = SkyAnchors.vanilla()
    assert sky.sunrise == pytest.approx(23215.0, abs=1.0)
    assert sky.sunset == pytest.approx(12785.0, abs=1.0)
    assert sun_elevation_deg(sky.sunrise) == pytest.approx(0.0, abs=1e-6)
    assert sun_elevation_deg(sky.sunset) == pytest.approx(0.0, abs=1e-6)


def test_crossings_are_not_at_ticks_0_and_12000():
    # The whole reason the twilight windows are offset.
    assert sun_elevation_deg(0) > 10.0
    assert sun_elevation_deg(12000) > 10.0


def test_civil_twilight_brackets_the_horizon():
    sky = SkyAnchors.vanilla()
    assert sun_elevation_deg(sky.civil_dawn) == pytest.approx(-6.0, abs=1e-6)
    assert sun_elevation_deg(sky.civil_dusk) == pytest.approx(-6.0, abs=1e-6)
    # Dawn comes before sunrise, dusk after sunset.
    assert tick_delta(sky.civil_dawn, sky.sunrise) > 0
    assert tick_delta(sky.sunset, sky.civil_dusk) > 0


@pytest.mark.parametrize("elevation", [-60.0, -6.0, 0.0, 15.0, 45.0, 89.0])
@pytest.mark.parametrize("rising", [True, False])
def test_tick_at_elevation_round_trips(elevation, rising):
    tick = tick_at_elevation(elevation, rising=rising)
    assert sun_elevation_deg(tick) == pytest.approx(elevation, abs=1e-5)


def test_elevation_rises_monotonically_from_dawn_to_noon():
    sky = SkyAnchors.vanilla()
    samples = [
        sun_elevation_deg((sky.sunrise + step) % TICKS_PER_DAY)
        for step in range(0, 6800, 200)
    ]
    assert samples == sorted(samples)


def test_tick_delta_takes_the_short_way_round():
    assert tick_delta(23900, 100) == pytest.approx(200)
    assert tick_delta(100, 23900) == pytest.approx(-200)
    assert tick_distance(23900, 100) == pytest.approx(200)


def test_interpolate_circular_walks_forwards_through_the_wrap():
    assert interpolate_circular(0.5, 23000, 1000) == pytest.approx(0.0)
    assert interpolate_circular(0.25, 0, 24000) == pytest.approx(6000.0)
