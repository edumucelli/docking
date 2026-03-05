"""Pure state and formatting logic for weather applet."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Mapping

from docking.applets.weather.api import AirQualityData, WeatherData
from docking.applets.weather.cities import CityEntry, load_cities
from docking.i18n import _

DEFAULT_ICON_NAME = "weather-few-clouds"


@dataclass(frozen=True, slots=True)
class WeatherPrefs:
    """Persisted weather applet preferences."""

    city_display: str = ""
    lat: float = 0.0
    lng: float = 0.0
    show_temperature: bool = True


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> WeatherPrefs:
    """Build preferences from persisted values."""
    if not prefs:
        return WeatherPrefs()
    return WeatherPrefs(
        city_display=str(prefs.get("city_display", "")),
        lat=float(prefs.get("lat", 0.0)),
        lng=float(prefs.get("lng", 0.0)),
        show_temperature=bool(prefs.get("show_temperature", True)),
    )


def prefs_payload(
    *,
    city_display: str,
    lat: float,
    lng: float,
    show_temperature: bool,
) -> dict[str, object]:
    """Build payload used by save_prefs()."""
    return {
        "city_display": city_display,
        "lat": lat,
        "lng": lng,
        "show_temperature": show_temperature,
    }


def build_forecast_url(*, lat: float, lng: float) -> str:
    """Build Open-Meteo forecast URL for the selected coordinates."""
    return (
        f"https://open-meteo.com/en/docs#latitude={lat}"
        f"&longitude={lng}"
        "&current=temperature_2m,weather_code"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min"
    )


def menu_header_label(
    *,
    city_display: str,
    weather: WeatherData | None,
) -> str:
    """Build the disabled menu header text."""
    if not weather:
        return city_display
    return _("{city}: {temp}°C").format(
        city=city_display, temp=f"{weather.temperature:.0f}"
    )


def build_tooltip(
    *,
    city_display: str,
    weather: WeatherData | None,
    air_quality: AirQualityData | None,
) -> str:
    """Build multi-line tooltip with current + daily forecast."""
    if not city_display:
        return _("Weather (no city selected)")
    if not weather:
        return _("{city}: loading...").format(city=city_display)

    lines = [city_display, f"{weather.temperature:.0f}°C, {weather.description}"]
    if air_quality:
        lines.append(_("Air: {label}").format(label=air_quality.label))
    for day in weather.daily:
        temp = f"{day.temp_min:.0f}/{day.temp_max:.0f}°C"
        lines.append(f"{day.date}: {temp}, {day.description}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def cached_cities() -> tuple[CityEntry, ...]:
    """Load city database on first access (cached)."""
    return tuple(load_cities())
