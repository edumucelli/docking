"""Pure state and formatting logic for weather applet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, NamedTuple

from docking.applets.weather.api import AirQualityData, WeatherData
from docking.applets.weather.cities import CityEntry, load_cities
from docking.i18n import _

DEFAULT_ICON_NAME = "weather-few-clouds"


class CityPref(NamedTuple):
    """A persisted city entry."""

    city_display: str
    lat: float
    lng: float


@dataclass(frozen=True, slots=True)
class WeatherPrefs:
    """Persisted weather applet preferences."""

    cities: tuple[CityPref, ...] = ()
    active_index: int = 0
    show_temperature: bool = True


def prefs_from_mapping(prefs: Mapping[str, Any] | None) -> WeatherPrefs:
    """Build preferences from persisted values.

    Migrates old scalar format (city_display/lat/lng) to cities list.
    """
    if not prefs:
        return WeatherPrefs()

    show_temperature = bool(prefs.get("show_temperature", True))

    # New format: cities list
    if "cities" in prefs:
        raw = prefs["cities"]
        cities = tuple(
            CityPref(
                city_display=str(c.get("city_display", "")),
                lat=float(c.get("lat", 0.0)),
                lng=float(c.get("lng", 0.0)),
            )
            for c in raw
            if isinstance(c, Mapping)
        )
        idx = int(prefs.get("active_index", 0))
        idx = max(0, min(idx, len(cities) - 1)) if cities else 0
        return WeatherPrefs(
            cities=cities,
            active_index=idx,
            show_temperature=show_temperature,
        )

    # Old scalar format migration
    city_display = str(prefs.get("city_display", ""))
    if city_display:
        city = CityPref(
            city_display=city_display,
            lat=float(prefs.get("lat", 0.0)),
            lng=float(prefs.get("lng", 0.0)),
        )
        return WeatherPrefs(
            cities=(city,),
            active_index=0,
            show_temperature=show_temperature,
        )

    return WeatherPrefs(show_temperature=show_temperature)


def prefs_payload(
    *,
    cities: tuple[CityPref, ...],
    active_index: int,
    show_temperature: bool,
) -> dict[str, object]:
    """Build payload used by save_prefs()."""
    return {
        "cities": [
            {"city_display": c.city_display, "lat": c.lat, "lng": c.lng} for c in cities
        ],
        "active_index": active_index,
        "show_temperature": show_temperature,
    }


def cycle_active_index(*, count: int, current: int, direction_up: bool) -> int:
    """Cycle through cities. Returns new index, wrapping around."""
    if count <= 1:
        return 0
    step = -1 if direction_up else 1
    return (current + step) % count


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
    fetch_failed: bool = False,
) -> str:
    """Build multi-line tooltip with current + daily forecast."""
    if not city_display:
        return _("Weather (no city selected)")
    if not weather:
        if fetch_failed:
            return _("{city}: unavailable").format(city=city_display)
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
