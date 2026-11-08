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

Uses standard-library HTTP and JSON with up to five retries.
Persistent response caching and polling share REFRESH_INTERVAL (5 min).
All functions are pure data -- no GTK dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import tempfile
import time
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import HTTPException
from pathlib import Path
from threading import Lock
from typing import NamedTuple, TypeVar, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from docking.applets.http import http_get_json
from docking.applets.weather import meta
from docking.core.json_types import JsonObject, JsonValue
from docking.log import get_logger, with_context
from docking.platform.environment import docking_cache_dir

log = with_context(get_logger(name="weather.api"), applet_id=meta.id)

# How often weather data is refreshed (seconds). Used for both the
# polling timer in the applet and the response cache expiry.
REFRESH_INTERVAL = 300  # 5 minutes
API_RETRY_COUNT = 5
API_RETRY_BACKOFF_FACTOR = 0.2
API_RETRY_AFTER_MAX_S = 60
API_TIMEOUT_S = 5

_CACHE_DIR = docking_cache_dir() / "weather"
_CACHE_WRITE_LOCK = Lock()

_API_URL = "https://api.open-meteo.com/v1/forecast"
_AQI_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

_Data = TypeVar("_Data")


def _object(value: JsonValue) -> JsonObject:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def _array(value: JsonValue) -> list[JsonValue]:
    if not isinstance(value, list):
        raise ValueError("Expected a JSON array")
    return value


def _number(value: JsonValue) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a numeric weather reading")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Expected a finite weather reading")
    return number


def _weather_code(value: JsonValue) -> int:
    number = _number(value)
    if int(number) != number:
        raise ValueError("Expected an integer WMO weather code")
    return int(number)


def _cache_path(url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _CACHE_DIR / "json-v1" / f"{key}.json"


def _read_cache(url: str) -> JsonObject | None:
    try:
        with _cache_path(url).open(encoding="utf-8") as handle:
            cached = _object(cast(JsonValue, json.load(handle)))
        age = time.time() - _number(cached["fetched_at"])
        if 0 <= age < REFRESH_INTERVAL:
            return _object(cached["payload"])
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        pass
    return None


def _prune_cache(current: Path) -> None:
    cutoff = time.time() - REFRESH_INTERVAL
    with suppress(OSError):
        for path in current.parent.glob("*.json"):
            if path == current:
                continue
            try:
                with path.open(encoding="utf-8") as handle:
                    cached = _object(cast(JsonValue, json.load(handle)))
                if _number(cached["fetched_at"]) <= cutoff:
                    path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError, OverflowError):
                continue


def _cache_payload(payload: JsonObject) -> JsonObject:
    # An unavailable optional UV reading must not prevent strict JSON caching
    # of otherwise valid air-quality data. Keep the decoded response unchanged.
    current = payload.get("current")
    if isinstance(current, dict):
        uv = current.get("uv_index")
        if isinstance(uv, float) and not math.isfinite(uv):
            return {**payload, "current": {**current, "uv_index": None}}
    return payload


def _write_cache(url: str, payload: JsonObject) -> None:
    temporary: Path | None = None
    try:
        path = _cache_path(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(
                {"fetched_at": time.time(), "payload": _cache_payload(payload)},
                handle,
                allow_nan=False,
            )
        # Serialize publication and pruning across concurrent worker threads.
        with _CACHE_WRITE_LOCK:
            temporary.replace(path)
            _prune_cache(path)
    except (OSError, ValueError):
        log.debug("Weather response could not be cached")
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if value.strip().isdigit():
            seconds = float(value)
        else:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - time.time()
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (ValueError, TypeError, OverflowError):
        return None


def _request_json(url: str) -> JsonObject:
    retries = 0
    while True:
        retry_after: float | None = None
        try:
            return _object(cast(JsonValue, http_get_json(url, timeout=API_TIMEOUT_S)))
        except HTTPError as error:
            try:
                header = error.headers.get("Retry-After")
                retryable = error.code in (500, 502, 504) or (
                    error.code in (413, 429, 503) and bool(header)
                )
                if retries >= API_RETRY_COUNT or not retryable:
                    raise
                retry_after = _retry_after(header)
                if retry_after and retry_after > API_RETRY_AFTER_MAX_S:
                    raise
            finally:
                error.close()
        except (OSError, HTTPException):
            if retries >= API_RETRY_COUNT:
                raise
        # Match the existing policy: no delay for the first retry, then
        # exponential backoff. A positive Retry-After overrides backoff.
        delay = retry_after or (
            API_RETRY_BACKOFF_FACTOR * 2**retries if retries else 0.0
        )
        if delay:
            time.sleep(delay)
        retries += 1


def _fetch(
    endpoint: str,
    params: dict[str, str | int | float],
    parse: Callable[[JsonObject], _Data],
) -> _Data:
    query = urlencode({**params, "timezone": "GMT", "timeformat": "unixtime"})
    url = f"{endpoint}?{query}"
    cached = _read_cache(url)
    if cached is not None:
        try:
            return parse(cached)
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            pass
    payload = _request_json(url)
    result = parse(payload)
    _write_cache(url, payload)
    return result


def _failure_reason(error: Exception) -> str:
    # Raw exception messages and tracebacks can contain URLs with coordinates.
    if isinstance(error, HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, URLError) and isinstance(error.reason, Exception):
        return type(error.reason).__name__
    return type(error).__name__


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


def _parse_weather(payload: JsonObject) -> WeatherData:
    current = _object(payload["current"])
    temp = _number(current["temperature_2m"])
    code = _weather_code(current["weather_code"])
    daily_data = _object(payload["daily"])
    arrays = [
        _array(daily_data[key])
        for key in ("time", "weather_code", "temperature_2m_max", "temperature_2m_min")
    ]
    daily: list[DailyForecast] = []
    for timestamp, day_code, high, low in zip(*arrays, strict=True):
        date = datetime.fromtimestamp(_number(timestamp), timezone.utc)
        day_name = date.strftime("%a")
        day_code_int = _weather_code(day_code)
        daily.append(
            DailyForecast(
                date=day_name,
                code=day_code_int,
                description=wmo_description(code=day_code_int),
                temp_max=round(_number(high), 1),
                temp_min=round(_number(low), 1),
            )
        )
    return WeatherData(
        temperature=round(temp, 1),
        weather_code=code,
        description=wmo_description(code=code),
        icon_name=wmo_icon_name(code=code),
        daily=daily,
    )


def fetch_weather(lat: float, lng: float) -> WeatherData | None:
    """Fetch current weather + 5-day forecast from Open-Meteo.

    Returns None on any API/network error. Responses cached per REFRESH_INTERVAL.
    """
    try:
        return _fetch(
            _API_URL,
            params={
                "latitude": lat,
                "longitude": lng,
                "current": "temperature_2m,weather_code",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min",
                "forecast_days": 5,
            },
            parse=_parse_weather,
        )
    except (
        OSError,
        HTTPException,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
    ) as error:
        log.bind(action="fetch_weather").warning(
            "Failed to fetch weather (%s)", _failure_reason(error)
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
        return _fetch(
            _AQI_URL,
            params={
                "latitude": lat,
                "longitude": lng,
                "current": "european_aqi,pm10,pm2_5,uv_index",
            },
            parse=_parse_air_quality,
        )
    except (
        OSError,
        HTTPException,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
    ) as error:
        log.bind(action="fetch_air_quality").warning(
            "Failed to fetch air quality (%s)", _failure_reason(error)
        )
        return None


def _parse_air_quality(payload: JsonObject) -> AirQualityData:
    current = _object(payload["current"])
    aqi = int(_number(current["european_aqi"]))
    return AirQualityData(
        aqi=aqi,
        pm2_5=round(_number(current["pm2_5"]), 1),
        pm10=round(_number(current["pm10"]), 1),
        label=aqi_label(aqi=aqi),
        uv_index=_optional_current_value(current),
    )


def _optional_current_value(current: JsonObject) -> float | None:
    try:
        return round(_number(current.get("uv_index")), 1)
    except (ValueError, OverflowError):
        return None
