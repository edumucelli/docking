"""Open-Meteo API client for weather applet.

Uses openmeteo_requests with requests-cache and retry (5 attempts).
Cache and poll interval share REFRESH_INTERVAL (5 min).
All functions are pure data -- no GTK dependency.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import openmeteo_requests
import requests_cache
from retry_requests import retry

# How often weather data is refreshed (seconds). Used for both the
# polling timer in the applet and the requests-cache expiry.
REFRESH_INTERVAL = 300  # 5 minutes

_CACHE_DIR = (
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    / "docking"
    / "weather"
)

_API_URL = "https://api.open-meteo.com/v1/forecast"


# -- WMO weather code mapping ------------------------------------------------


class WmoEntry(NamedTuple):
    """WMO weather code mapping: human description + GTK icon name."""

    description: str
    icon_name: str


_WMO_CODES: dict[int, WmoEntry] = {
    0: WmoEntry("Clear sky", "weather-clear"),
    1: WmoEntry("Mainly clear", "weather-few-clouds"),
    2: WmoEntry("Partly cloudy", "weather-few-clouds"),
    3: WmoEntry("Overcast", "weather-overcast"),
    45: WmoEntry("Fog", "weather-fog"),
    48: WmoEntry("Depositing rime fog", "weather-fog"),
    51: WmoEntry("Light drizzle", "weather-showers-scattered"),
    53: WmoEntry("Moderate drizzle", "weather-showers-scattered"),
    55: WmoEntry("Dense drizzle", "weather-showers-scattered"),
    56: WmoEntry("Light freezing drizzle", "weather-showers-scattered"),
    57: WmoEntry("Dense freezing drizzle", "weather-showers-scattered"),
    61: WmoEntry("Slight rain", "weather-showers"),
    63: WmoEntry("Moderate rain", "weather-showers"),
    65: WmoEntry("Heavy rain", "weather-showers"),
    66: WmoEntry("Light freezing rain", "weather-showers"),
    67: WmoEntry("Heavy freezing rain", "weather-showers"),
    71: WmoEntry("Slight snowfall", "weather-snow"),
    73: WmoEntry("Moderate snowfall", "weather-snow"),
    75: WmoEntry("Heavy snowfall", "weather-snow"),
    77: WmoEntry("Snow grains", "weather-snow"),
    80: WmoEntry("Slight rain showers", "weather-showers"),
    81: WmoEntry("Moderate rain showers", "weather-showers"),
    82: WmoEntry("Violent rain showers", "weather-showers"),
    85: WmoEntry("Slight snow showers", "weather-snow"),
    86: WmoEntry("Heavy snow showers", "weather-snow"),
    95: WmoEntry("Thunderstorm", "weather-storm"),
    96: WmoEntry("Thunderstorm with slight hail", "weather-storm"),
    99: WmoEntry("Thunderstorm with heavy hail", "weather-storm"),
}


def wmo_description(code: int) -> str:
    """Human-readable description for a WMO weather code."""
    return _WMO_CODES.get(code, WmoEntry("Unknown", "weather-few-clouds")).description


def wmo_icon_name(code: int) -> str:
    """GTK icon name for a WMO weather code."""
    return _WMO_CODES.get(code, WmoEntry("Unknown", "weather-few-clouds")).icon_name


# -- Data types --------------------------------------------------------------


class DailyForecast(NamedTuple):
    """One day's forecast summary."""

    date: str  # "Mon", "Tue", etc.
    code: int
    description: str
    temp_max: float
    temp_min: float


class WeatherData(NamedTuple):
    """Current weather + daily forecast."""

    temperature: float
    weather_code: int
    description: str
    icon_name: str
    daily: list[DailyForecast]


# -- API client --------------------------------------------------------------


def _get_client() -> openmeteo_requests.Client:
    """Create or return cached API client with retry and request caching."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = str(_CACHE_DIR / "responses")
    cache_session = requests_cache.CachedSession(
        cache_path, expire_after=REFRESH_INTERVAL
    )
    retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
    return openmeteo_requests.Client(session=retry_session)


def fetch_weather(lat: float, lng: float) -> WeatherData | None:
    """Fetch current weather + 5-day forecast from Open-Meteo.

    Returns None on any API/network error. Responses cached per REFRESH_INTERVAL.
    """
    try:
        client = _get_client()
        responses = client.weather_api(
            _API_URL,
            params={
                "latitude": lat,
                "longitude": lng,
                "current": ["temperature_2m", "weather_code"],
                "daily": [
                    "weather_code",
                    "temperature_2m_max",
                    "temperature_2m_min",
                ],
                "forecast_days": 5,
            },
        )
        resp = responses[0]

        # Current conditions
        current = resp.Current()
        temp = current.Variables(0).Value()
        code = int(current.Variables(1).Value())

        # Daily forecast
        daily_data = resp.Daily()
        daily: list[DailyForecast] = []
        for i in range(daily_data.Variables(0).ValuesLength()):
            # Daily timestamps are Unix epoch at midnight UTC
            ts = daily_data.Time() + i * daily_data.Interval()
            day_name = datetime.utcfromtimestamp(ts).strftime("%a")
            day_code = int(daily_data.Variables(0).Values(i))
            day_max = daily_data.Variables(1).Values(i)
            day_min = daily_data.Variables(2).Values(i)
            daily.append(
                DailyForecast(
                    date=day_name,
                    code=day_code,
                    description=wmo_description(code=day_code),
                    temp_max=round(day_max, 1),
                    temp_min=round(day_min, 1),
                )
            )

        return WeatherData(
            temperature=round(temp, 1),
            weather_code=code,
            description=wmo_description(code=code),
            icon_name=wmo_icon_name(code=code),
            daily=daily,
        )
    except (OSError, ValueError, KeyError, IndexError, AttributeError):
        return None
