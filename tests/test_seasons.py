from datetime import datetime, timedelta, timezone

import pytest

from mcwall.seasons import Phase, Season, SubSeason, solar_event, sub_season_at

CHICAGO = 41.88
SYDNEY = -33.87


def utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


# Published instants (USNO / timeanddate), to the minute.
@pytest.mark.parametrize(
    "year,event,expected",
    [
        (2026, "march_equinox", utc(2026, 3, 20, 0) + timedelta(hours=14, minutes=46)),
        (2026, "june_solstice", utc(2026, 6, 21, 0) + timedelta(hours=8, minutes=25)),
        (2026, "september_equinox", utc(2026, 9, 23, 0) + timedelta(minutes=6)),
        (2026, "december_solstice", utc(2026, 12, 21, 0) + timedelta(hours=20, minutes=50)),
        (2025, "march_equinox", utc(2025, 3, 20, 0) + timedelta(hours=9, minutes=1)),
        (2027, "june_solstice", utc(2027, 6, 21, 0) + timedelta(hours=14, minutes=11)),
    ],
)
def test_solar_events_match_published_instants(year, event, expected):
    computed = solar_event(year, event)
    assert abs((computed - expected).total_seconds()) < 120


def test_twelve_distinct_sub_seasons():
    everything = SubSeason.all()
    assert len(everything) == 12
    assert len({s.key for s in everything}) == 12
    assert {s.index for s in everything} == set(range(12))


def test_key_round_trip_and_serene_name():
    cell = SubSeason(Season.AUTUMN, Phase.MID)
    assert cell.key == "mid_autumn"
    assert cell.serene_name == "MID_AUTUMN"
    assert SubSeason.from_key("mid_autumn") == cell


def test_hemispheres_are_six_months_apart():
    moment = utc(2026, 1, 15)
    north = sub_season_at(moment, CHICAGO)
    south = sub_season_at(moment, SYDNEY)
    assert north.season is Season.WINTER
    assert south.season is Season.SUMMER
    assert north.phase is south.phase


def test_season_opens_on_its_solar_event():
    equinox = solar_event(2026, "march_equinox")
    just_before = sub_season_at(equinox - timedelta(hours=1), CHICAGO)
    just_after = sub_season_at(equinox + timedelta(hours=1), CHICAGO)
    assert just_before == SubSeason(Season.WINTER, Phase.LATE)
    assert just_after == SubSeason(Season.SPRING, Phase.EARLY)


def test_phases_split_a_season_into_thirds():
    start = solar_event(2026, "june_solstice")
    end = solar_event(2026, "september_equinox")
    span = end - start
    for fraction, expected in [(0.1, Phase.EARLY), (0.5, Phase.MID), (0.9, Phase.LATE)]:
        moment = start + span * fraction
        assert sub_season_at(moment, CHICAGO) == SubSeason(Season.SUMMER, expected)


def test_a_full_year_visits_every_sub_season():
    moment = utc(2026, 1, 1)
    seen = set()
    for _ in range(365):
        seen.add(sub_season_at(moment, CHICAGO))
        moment += timedelta(days=1)
    assert len(seen) == 12


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        sub_season_at(datetime(2026, 5, 1, 12), CHICAGO)
