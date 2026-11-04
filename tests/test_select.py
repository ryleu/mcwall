from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from mcwall.grid import Weather, nearest_slot, slot_ticks, weather_from_wmo
from mcwall.library import GridCell, Library, Manifest
from mcwall.seasons import Phase, Season, SubSeason
from mcwall.select import Conditions, Mode1Selector, NoAssetAvailable

CHICAGO = (41.88, -87.63, ZoneInfo("America/Chicago"))
TICKS = slot_ticks()


def manifest(loop_ticks=()):
    return Manifest(
        pack_id="complementaryunbound-r5-9-3",
        pack_name="ComplementaryUnbound_r5.9.3.zip",
        settings_hash="deadbeef",
        width=2880,
        height=1800,
        image_format="webp",
        sub_seasons=[s.key for s in SubSeason.all()],
        weathers=[w.value for w in Weather],
        ticks=TICKS,
        loop_ticks=list(loop_ticks),
    )


def library(stills=None, loops=None, loop_ticks=()):
    return Library(
        Path("/library"),
        manifest(loop_ticks),
        stills=set(stills or []),
        loops=set(loops or []),
    )


def selector(lib, allow_loops=True):
    latitude, longitude, _ = CHICAGO
    return Mode1Selector(lib, latitude, longitude, allow_loops=allow_loops)


def moment(month=11, day=3, hour=12):
    return datetime(2026, month, day, hour, tzinfo=CHICAGO[2])


# --- weather codes -------------------------------------------------------


@pytest.mark.parametrize(
    "code,expected",
    [
        (0, Weather.CLEAR), (1, Weather.CLEAR), (2, Weather.CLEAR),
        # Overcast and fog are rain: the library has no cloudy sky, and
        # sunshine under a grey sky is the more obviously wrong answer.
        (3, Weather.RAIN), (45, Weather.RAIN), (48, Weather.RAIN),
        (51, Weather.RAIN), (61, Weather.RAIN), (67, Weather.RAIN),
        (71, Weather.RAIN), (77, Weather.RAIN), (80, Weather.RAIN), (86, Weather.RAIN),
        (95, Weather.THUNDER), (99, Weather.THUNDER),
    ],
)
def test_wmo_codes_fold_onto_three_states(code, expected):
    assert weather_from_wmo(code) is expected


def test_unknown_wmo_code_is_an_error():
    with pytest.raises(ValueError):
        weather_from_wmo(120)
    with pytest.raises(ValueError):
        weather_from_wmo(-1)


# --- slots ---------------------------------------------------------------


def test_nearest_slot_wraps_around_midnight():
    assert nearest_slot(23990, [100, 12000, 23800]) == 100
    assert nearest_slot(10, [100, 12000, 23800]) == 100


def test_nearest_slot_is_deterministic_on_ties():
    assert nearest_slot(500, [0, 1000]) == 0


# --- selection -----------------------------------------------------------


def test_exact_cell_wins():
    sel = selector(library())
    wanted = sel.target_cell(moment(), Conditions(Weather.RAIN))
    sel.library.stills = {wanted}
    choice = sel.select(moment(), Conditions(Weather.RAIN))
    assert choice.exact and choice.kind == "still"
    assert choice.cell == wanted
    assert choice.reasons == ()


def test_thunder_falls_back_to_rain_before_clear():
    sel = selector(library())
    wanted = sel.target_cell(moment(), Conditions(Weather.THUNDER))
    rain = GridCell(wanted.sub_season, Weather.RAIN, wanted.tick)
    clear = GridCell(wanted.sub_season, Weather.CLEAR, wanted.tick)
    sel.library.stills = {rain, clear}
    choice = sel.select(moment(), Conditions(Weather.THUNDER))
    assert choice.cell == rain
    assert choice.reasons == ("weather thunder->rain",)


def test_weather_fallback_runs_out_at_clear():
    sel = selector(library())
    wanted = sel.target_cell(moment(), Conditions(Weather.THUNDER))
    clear = GridCell(wanted.sub_season, Weather.CLEAR, wanted.tick)
    sel.library.stills = {clear}
    choice = sel.select(moment(), Conditions(Weather.THUNDER))
    assert choice.cell == clear
    assert choice.reasons == ("weather thunder->rain", "weather rain->clear")


def test_weather_is_substituted_before_time():
    """A clear sky now beats a rainy sky from a different part of the day."""
    sel = selector(library())
    wanted = sel.target_cell(moment(), Conditions(Weather.RAIN))
    other_tick = next(t for t in TICKS if t != wanted.tick)
    sel.library.stills = {
        GridCell(wanted.sub_season, Weather.CLEAR, wanted.tick),
        GridCell(wanted.sub_season, Weather.RAIN, other_tick),
    }
    choice = sel.select(moment(), Conditions(Weather.RAIN))
    assert choice.cell.tick == wanted.tick
    assert choice.cell.weather is Weather.CLEAR


def test_time_fallback_picks_the_nearest_slot():
    sel = selector(library())
    wanted = sel.target_cell(moment(), Conditions(Weather.CLEAR))
    ordered = sorted(TICKS, key=lambda t: abs(((t - wanted.tick + 12000) % 24000) - 12000))
    near, far = ordered[1], ordered[-1]
    sel.library.stills = {
        GridCell(wanted.sub_season, Weather.CLEAR, near),
        GridCell(wanted.sub_season, Weather.CLEAR, far),
    }
    choice = sel.select(moment(), Conditions(Weather.CLEAR))
    assert choice.cell.tick == near
    assert not choice.exact
    assert choice.reasons[-1].startswith("slot ")


def test_season_is_never_substituted():
    sel = selector(library())
    wanted = sel.target_cell(moment(), Conditions(Weather.CLEAR))
    wrong_season = SubSeason(Season.SUMMER, Phase.MID)
    assert wanted.sub_season != wrong_season
    sel.library.stills = {
        GridCell(wrong_season, w, t) for w in Weather for t in TICKS
    }
    with pytest.raises(NoAssetAvailable):
        sel.select(moment(), Conditions(Weather.CLEAR))


def test_empty_library_raises():
    with pytest.raises(NoAssetAvailable):
        selector(library()).select(moment(), Conditions(Weather.CLEAR))


def test_selection_never_launches_anything(monkeypatch):
    """The display path must not shell out. Guard it in the test, not by hope."""
    import subprocess

    def explode(*args, **kwargs):  # pragma: no cover - only runs on regression
        raise AssertionError("select() spawned a process")

    monkeypatch.setattr(subprocess, "run", explode)
    monkeypatch.setattr(subprocess, "Popen", explode)
    sel = selector(library())
    wanted = sel.target_cell(moment(), Conditions(Weather.CLEAR))
    sel.library.stills = {wanted}
    assert sel.select(moment(), Conditions(Weather.CLEAR)).cell == wanted


# --- loops ---------------------------------------------------------------


def test_loop_used_on_ac_for_rain():
    loop_ticks = TICKS[::4]
    sel = selector(library(loop_ticks=loop_ticks))
    wanted = sel.target_cell(moment(), Conditions(Weather.RAIN))
    cell = GridCell(wanted.sub_season, Weather.RAIN, loop_ticks[0])
    sel.library.loops = {cell}
    sel.library.stills = {wanted}
    choice = sel.select(moment(), Conditions(Weather.RAIN, on_ac=True))
    assert choice.kind == "loop" and choice.cell == cell


def test_battery_forces_stills():
    loop_ticks = TICKS[::4]
    sel = selector(library(loop_ticks=loop_ticks))
    wanted = sel.target_cell(moment(), Conditions(Weather.RAIN))
    sel.library.loops = {GridCell(wanted.sub_season, Weather.RAIN, loop_ticks[0])}
    sel.library.stills = {wanted}
    choice = sel.select(moment(), Conditions(Weather.RAIN, on_ac=False))
    assert choice.kind == "still"


def test_clear_weather_never_loops():
    loop_ticks = TICKS[::4]
    sel = selector(library(loop_ticks=loop_ticks))
    wanted = sel.target_cell(moment(), Conditions(Weather.CLEAR))
    sel.library.loops = {GridCell(wanted.sub_season, Weather.CLEAR, loop_ticks[0])}
    sel.library.stills = {wanted}
    assert sel.select(moment(), Conditions(Weather.CLEAR)).kind == "still"


# --- both hemispheres ----------------------------------------------------


@pytest.mark.parametrize(
    "latitude,longitude,expected_season",
    [(41.88, -87.63, Season.AUTUMN), (-33.87, 151.21, Season.SPRING)],
)
def test_target_cell_follows_the_hemisphere(latitude, longitude, expected_season):
    sel = Mode1Selector(library(), latitude, longitude)
    cell = sel.target_cell(
        datetime(2026, 11, 3, 18, tzinfo=timezone.utc), Conditions(Weather.CLEAR)
    )
    assert cell.sub_season.season is expected_season
    assert cell.tick in TICKS
