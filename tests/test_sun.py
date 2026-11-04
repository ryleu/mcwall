"""The plan's hard requirement: real sunrise must land on the vanilla sunrise."""

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from mcwall.grid import DEFAULT_WINDOWS, nearest_slot, slot_ticks
from mcwall.mctime import SkyAnchors, tick_distance
from mcwall.sun import day_anchors, tick_for_moment


def anchors_by_name(latitude, longitude, day):
    return {a.name: a for a in day_anchors(latitude, longitude, day)}

CHICAGO = (41.88, -87.63, ZoneInfo("America/Chicago"))
SYDNEY = (-33.87, 151.21, ZoneInfo("Australia/Sydney"))
LONGYEARBYEN = (78.22, 15.63, ZoneInfo("Arctic/Longyearbyen"))

FIXTURE_DAYS = [
    date(2026, 1, 15),
    date(2026, 3, 20),
    date(2026, 6, 21),
    date(2026, 9, 23),
    date(2026, 11, 3),
    date(2026, 12, 21),
]


@pytest.mark.parametrize("place", [CHICAGO, SYDNEY], ids=["chicago", "sydney"])
@pytest.mark.parametrize("day", FIXTURE_DAYS, ids=str)
def test_real_sunrise_lands_on_the_vanilla_sunrise_slot(place, day):
    latitude, longitude, _ = place
    sky = SkyAnchors.vanilla()
    ticks = slot_ticks()

    anchors = anchors_by_name(latitude, longitude, day)
    tick = tick_for_moment(anchors["sunrise"].when, latitude, longitude)

    assert tick == pytest.approx(sky.sunrise, abs=1.0)
    # The slot the display path would actually pick must be the one nearest the
    # horizon crossing.
    assert nearest_slot(tick, ticks) == nearest_slot(sky.sunrise, ticks)


@pytest.mark.parametrize("place", [CHICAGO, SYDNEY], ids=["chicago", "sydney"])
@pytest.mark.parametrize("day", FIXTURE_DAYS, ids=str)
def test_every_anchor_maps_to_its_own_tick(place, day):
    latitude, longitude, _ = place
    for anchor in day_anchors(latitude, longitude, day):
        tick = tick_for_moment(anchor.when, latitude, longitude)
        assert tick_distance(tick, anchor.tick) < 1.0, anchor.name


@pytest.mark.parametrize("place", [CHICAGO, SYDNEY], ids=["chicago", "sydney"])
def test_mapping_advances_monotonically_through_a_day(place):
    latitude, longitude, tz = place
    moment = datetime(2026, 11, 3, tzinfo=tz)
    previous = tick_for_moment(moment, latitude, longitude)
    walked = 0.0
    for _ in range(24 * 12):
        moment += timedelta(minutes=5)
        current = tick_for_moment(moment, latitude, longitude)
        step = (current - previous) % 24000
        assert 0 < step < 1000, f"jump of {step} ticks at {moment}"
        walked += step
        previous = current
    # One real day covers one vanilla day.
    assert walked == pytest.approx(24000, abs=50)


def test_noon_is_tick_6000_and_midnight_is_18000():
    latitude, longitude, _ = CHICAGO
    anchors = anchors_by_name(latitude, longitude, date(2026, 7, 1))
    assert tick_for_moment(anchors["solar_noon"].when, latitude, longitude) == pytest.approx(
        6000, abs=1
    )
    assert tick_for_moment(
        anchors["solar_midnight"].when, latitude, longitude
    ) == pytest.approx(18000, abs=1)


def test_summer_and_winter_sunrises_differ_in_real_time_but_not_in_tick():
    latitude, longitude, tz = CHICAGO
    summer = anchors_by_name(latitude, longitude, date(2026, 6, 21))["sunrise"]
    winter = anchors_by_name(latitude, longitude, date(2026, 12, 21))["sunrise"]
    summer_local = summer.when.astimezone(tz)
    winter_local = winter.when.astimezone(tz)
    # Seasonal day length is absorbed by the anchors, not by the grid.
    assert winter_local.hour - summer_local.hour >= 1
    assert summer.tick == winter.tick


def test_polar_day_falls_back_instead_of_raising():
    latitude, longitude, _ = LONGYEARBYEN
    moment = datetime(2026, 6, 21, 12, tzinfo=timezone.utc)
    tick = tick_for_moment(moment, latitude, longitude)
    assert 0 <= tick < 24000


def test_slot_windows_cover_the_whole_day_without_duplicates():
    ticks = slot_ticks()
    assert len(ticks) == 48
    assert len(set(ticks)) == 48
    assert sum(w.slots for w in DEFAULT_WINDOWS) == 48
    # No gap wider than the coarsest window spacing.
    gaps = [(b - a) for a, b in zip(ticks, ticks[1:])]
    gaps.append(ticks[0] + 24000 - ticks[-1])
    assert max(gaps) <= 900


@pytest.mark.network
def test_astral_agrees_with_skyfield():
    """Cross-check the sun provider against a full ephemeris."""
    skyfield_api = pytest.importorskip("skyfield.api")
    from skyfield import almanac

    latitude, longitude, _ = CHICAGO
    loader = skyfield_api.Loader("var/skyfield")
    ephemeris = loader("de421.bsp")
    timescale = loader.timescale()
    observer = ephemeris["earth"] + skyfield_api.wgs84.latlon(latitude, longitude)

    for day in FIXTURE_DAYS:
        start = timescale.utc(day.year, day.month, day.day)
        end = timescale.utc(day.year, day.month, day.day + 1)
        times, _ = almanac.find_risings(observer, ephemeris["sun"], start, end)
        reference = times[0].utc_datetime()
        ours = anchors_by_name(latitude, longitude, day)["sunrise"].when
        assert abs((ours - reference).total_seconds()) < 30, day
