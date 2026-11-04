"""Current conditions from Open-Meteo, and one-time IP geolocation.

Both are cached on disk. The display path must never block on the network: if a
fetch fails or times out, we fall back to the last cached value and, failing
that, to clear weather. A wrong sky beats no wallpaper.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import STATE_DIR
from .grid import Weather, severity, weather_from_wmo

log = logging.getLogger(__name__)

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GEO_URLS = (
    ("https://ipapi.co/json/", "latitude", "longitude"),
    ("http://ip-api.com/json/", "lat", "lon"),
)
TIMEOUT = 8.0

# How far back to look for precipitation. An instantaneous weather code is a
# poor match for what a person means by "it is raining": showers come in
# bursts, and sampling a dry gap between two of them reads as clear while
# someone standing outside is still getting wet. Open-Meteo's 15-minute
# nowcast is radar-derived, so taking the most notable sky in the recent past
# both tracks what it feels like and stops the wallpaper flickering.
RAIN_LOOKBACK_MINUTES = 30
NOWCAST_STEP = timedelta(minutes=15)


@dataclass(frozen=True)
class Observation:
    weather: Weather
    code: int
    observed_at: datetime
    stale: bool = False
    source: str = "open-meteo"


def _cache_path(name: str) -> Path:
    return STATE_DIR / name


def _read_cache(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload))
    tmp.replace(path)


def detect_location() -> tuple[float, float]:
    """Approximate latitude and longitude from the public IP address.

    Called once by ``mcwall init``; the result is written to the user config so
    nothing geolocates at display time.
    """
    import httpx

    errors = []
    for url, lat_key, lon_key in GEO_URLS:
        try:
            response = httpx.get(url, timeout=TIMEOUT, follow_redirects=True)
            response.raise_for_status()
            data = response.json()
            return float(data[lat_key]), float(data[lon_key])
        except Exception as exc:  # noqa: BLE001 - any failure moves to the next
            errors.append(f"{url}: {exc}")
    raise RuntimeError("IP geolocation failed:\n  " + "\n  ".join(errors))


def _recent_weather(
    block: dict | None, now: datetime, lookback: timedelta
) -> tuple[Weather, int] | None:
    """The most notable sky in the recent past, or None if unavailable.

    Each entry is stamped with the *start* of its 15-minute span, so a span
    counts when any of it falls inside the window. Entries that start after
    ``now`` are forecasts and are ignored — the wallpaper reports the weather,
    it does not predict it.
    """
    if not block or "time" not in block:
        return None

    cutoff = now - lookback
    worst: tuple[Weather, int] | None = None
    for stamp, precipitation, code in zip(
        block["time"],
        block.get("precipitation") or [],
        block.get("weather_code") or [],
    ):
        if code is None:
            continue
        start = datetime.fromisoformat(stamp)
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if start > now or start + NOWCAST_STEP <= cutoff:
            continue

        weather = weather_from_wmo(int(code))
        # Precipitation with a dry-looking code still means it rained.
        if weather is Weather.CLEAR and (precipitation or 0) > 0:
            weather = Weather.RAIN
        if worst is None or severity(weather) > severity(worst[0]):
            worst = (weather, int(code))
    return worst


def current(
    latitude: float,
    longitude: float,
    max_age_seconds: int = 300,
    now: datetime | None = None,
    lookback: timedelta | None = None,
) -> Observation:
    """Current weather, from cache when it is fresh enough.

    The cache window matches the display timer's cadence, so a shower is not
    waiting out a stale entry on top of the lookback.
    """
    now = now or datetime.now(timezone.utc)
    lookback = lookback if lookback is not None else timedelta(
        minutes=RAIN_LOOKBACK_MINUTES
    )
    path = _cache_path("weather.json")
    cached = _read_cache(path)

    if cached:
        fetched = datetime.fromisoformat(cached["fetched_at"])
        if now - fetched < timedelta(seconds=max_age_seconds):
            return Observation(
                weather=Weather(cached["weather"]),
                code=cached["code"],
                observed_at=fetched,
                source="cache",
            )

    try:
        import httpx

        steps = RAIN_LOOKBACK_MINUTES // 15 + 1
        response = httpx.get(
            WEATHER_URL,
            params={
                "latitude": round(latitude, 3),
                "longitude": round(longitude, 3),
                "current": "weather_code",
                "minutely_15": "precipitation,weather_code",
                "past_minutely_15": steps,
                "forecast_minutely_15": 1,
                "timezone": "UTC",
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
        code = int(payload["current"]["weather_code"])
        weather = weather_from_wmo(code)

        recent = _recent_weather(payload.get("minutely_15"), now, lookback)
        if recent is not None and severity(recent[0]) > severity(weather):
            weather, code = recent
        _write_cache(
            path,
            {
                "weather": weather.value,
                "code": code,
                "fetched_at": now.isoformat(),
            },
        )
        return Observation(weather=weather, code=code, observed_at=now)
    except Exception as exc:  # noqa: BLE001 - never let the display path fail
        log.warning("weather fetch failed (%s); using fallback", exc)

    if cached:
        return Observation(
            weather=Weather(cached["weather"]),
            code=cached["code"],
            observed_at=datetime.fromisoformat(cached["fetched_at"]),
            stale=True,
            source="stale-cache",
        )
    return Observation(
        weather=Weather.CLEAR, code=0, observed_at=now, stale=True, source="default"
    )
