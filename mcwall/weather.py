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
from .grid import Weather, weather_from_wmo

log = logging.getLogger(__name__)

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
GEO_URLS = (
    ("https://ipapi.co/json/", "latitude", "longitude"),
    ("http://ip-api.com/json/", "lat", "lon"),
)
TIMEOUT = 8.0


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


def current(
    latitude: float,
    longitude: float,
    max_age_seconds: int = 900,
    now: datetime | None = None,
) -> Observation:
    """Current weather, from cache when it is fresh enough."""
    now = now or datetime.now(timezone.utc)
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

        response = httpx.get(
            WEATHER_URL,
            params={
                "latitude": round(latitude, 3),
                "longitude": round(longitude, 3),
                "current": "weather_code",
                "timezone": "UTC",
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        code = int(response.json()["current"]["weather_code"])
        weather = weather_from_wmo(code)
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
