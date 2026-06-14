# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Open-Meteo API client for weather applet.

Uses openmeteo_requests with requests-cache and retry (5 attempts).
Cache and poll interval share REFRESH_INTERVAL (5 min).
All functions are pure data -- no GTK dependency.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, NamedTuple, cast

from docking.applets.weather import meta
from docking.core.paths import ensure_dir
from docking.log import get_logger, with_context
from docking.platform.environment import docking_cache_dir

log = with_context(get_logger(name="weather.api"), applet_id=meta.id)

# How often weather data is refreshed (seconds). Used for both the
# polling timer in the applet and the requests-cache expiry.
REFRESH_INTERVAL = 300  # 5 minutes
API_RETRY_COUNT = 5
API_RETRY_BACKOFF_FACTOR = 0.2

_CACHE_DIR = docking_cache_dir() / "weather"

_API_URL = "https://api.open-meteo.com/v1/forecast"
_AQI_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"


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


@lru_cache(maxsize=1)
def _get_client() -> Any:
    """Return cached API client with retry and request caching."""
    import openmeteo_requests
    import requests_cache
    from retry_requests import retry

    ensure_dir(_CACHE_DIR)
    cache_path = str(_CACHE_DIR / "responses")
    # Three-layer stack: openmeteo_requests wraps retry_requests wraps
    # requests_cache. Cache avoids redundant HTTP; retry handles failures.
    cache_session = requests_cache.CachedSession(
        cache_path, expire_after=REFRESH_INTERVAL
    )
    retry_session = retry(
        cache_session,
        retries=API_RETRY_COUNT,
        backoff_factor=API_RETRY_BACKOFF_FACTOR,
    )
    return openmeteo_requests.Client(session=cast(Any, retry_session))


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
            day_name = datetime.fromtimestamp(ts, timezone.utc).strftime("%a")
            day_code = int(cast(float, daily_data.Variables(0).Values(i)))
            day_max = float(cast(float, daily_data.Variables(1).Values(i)))
            day_min = float(cast(float, daily_data.Variables(2).Values(i)))
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
    except Exception:
        log.bind(action="fetch_weather").warning(
            "Failed to fetch weather", exc_info=True
        )
        return None


# -- Air quality ---------------------------------------------------------------


class AirQualityData(NamedTuple):
    """Current air quality readings."""

    aqi: int  # European AQI index
    pm2_5: float  # Fine particulate (μg/m³)
    pm10: float  # Particulate (μg/m³)
    label: str  # Human-readable level
    uv_index: float | None = None  # Current UV index


def aqi_label(aqi: int) -> str:
    """Map European AQI value to a human-readable level."""
    if aqi <= 20:
        return "Good"
    if aqi <= 40:
        return "Fair"
    if aqi <= 60:
        return "Moderate"
    if aqi <= 80:
        return "Poor"
    if aqi <= 100:
        return "Very Poor"
    return "Extremely Poor"


def fetch_air_quality(lat: float, lng: float) -> AirQualityData | None:
    """Fetch current air quality from Open-Meteo.

    Returns None on any API/network error. Responses cached per REFRESH_INTERVAL.
    """
    try:
        client = _get_client()
        responses = client.weather_api(
            _AQI_URL,
            params={
                "latitude": lat,
                "longitude": lng,
                "current": ["european_aqi", "pm10", "pm2_5", "uv_index"],
            },
        )
        current = responses[0].Current()
        aqi = int(current.Variables(0).Value())
        pm10 = round(current.Variables(1).Value(), 1)
        pm2_5 = round(current.Variables(2).Value(), 1)
        uv_index = _optional_current_value(current, index=3)
        return AirQualityData(
            aqi=aqi,
            pm2_5=pm2_5,
            pm10=pm10,
            label=aqi_label(aqi=aqi),
            uv_index=uv_index,
        )
    except Exception:
        log.bind(action="fetch_air_quality").warning(
            "Failed to fetch air quality",
            exc_info=True,
        )
        return None


def _optional_current_value(current: Any, *, index: int) -> float | None:
    try:
        return round(float(current.Variables(index).Value()), 1)
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
