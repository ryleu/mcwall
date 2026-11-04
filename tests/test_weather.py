"""What counts as rain, and how far back we look for it."""

from datetime import datetime, timedelta, timezone

import pytest

from mcwall.grid import Weather, severity
from mcwall.weather import _recent_weather

NOW = datetime(2026, 11, 4, 18, 50, tzinfo=timezone.utc)
WINDOW = timedelta(minutes=30)


def block(*rows):
    return {
        "time": [r[0] for r in rows],
        "precipitation": [r[1] for r in rows],
        "weather_code": [r[2] for r in rows],
    }


def test_a_dry_gap_between_showers_still_counts_as_rain():
    """The case that started this: showers in bursts, sampled mid-gap.

    Open-Meteo's instantaneous code said overcast and the airport agreed,
    while it was plainly raining outside, because both landed in the gap.
    """
    observed = _recent_weather(
        block(
            ("2026-11-04T18:15", 1.3, 63),  # raining
            ("2026-11-04T18:30", 0.0, 2),   # gap, genuinely clear sky
            ("2026-11-04T18:45", 0.0, 2),   # gap — sampled here
        ),
        NOW,
        WINDOW,
    )
    assert observed is not None
    assert observed[0] is Weather.RAIN


def test_rain_older_than_the_window_is_forgotten():
    """Otherwise the sky never dries out."""
    observed = _recent_weather(
        block(
            ("2026-11-04T17:45", 5.0, 65),  # ends 18:00, outside a 18:20 cutoff
            ("2026-11-04T18:30", 0.0, 2),
            ("2026-11-04T18:45", 0.0, 2),
        ),
        NOW,
        WINDOW,
    )
    assert observed[0] is Weather.CLEAR


def test_a_span_straddling_the_cutoff_still_counts():
    """A span is stamped with its start, so part of it can be inside."""
    observed = _recent_weather(
        block(("2026-11-04T18:15", 1.0, 61)), NOW, WINDOW
    )
    assert observed[0] is Weather.RAIN


def test_forecast_entries_are_ignored():
    """mcwall reports the weather; it does not predict it."""
    observed = _recent_weather(
        block(
            ("2026-11-04T18:45", 0.0, 2),
            ("2026-11-04T19:00", 2.0, 80),  # the future
        ),
        NOW,
        WINDOW,
    )
    assert observed[0] is Weather.CLEAR


def test_thunder_outranks_rain_in_the_window():
    observed = _recent_weather(
        block(
            ("2026-11-04T18:30", 1.0, 61),
            ("2026-11-04T18:45", 2.0, 95),
        ),
        NOW,
        WINDOW,
    )
    assert observed[0] is Weather.THUNDER


def test_precipitation_with_a_dry_code_is_rain():
    """The code and the millimetres disagree; the millimetres are wetter."""
    observed = _recent_weather(
        block(("2026-11-04T18:45", 0.4, 2)), NOW, WINDOW
    )
    assert observed[0] is Weather.RAIN


def test_a_missing_block_is_not_an_error():
    """A nowcast is an improvement, not a dependency."""
    assert _recent_weather(None, NOW, WINDOW) is None
    assert _recent_weather({}, NOW, WINDOW) is None


def test_severity_orders_the_three_skies():
    assert severity(Weather.CLEAR) < severity(Weather.RAIN) < severity(Weather.THUNDER)
