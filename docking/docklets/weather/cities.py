"""City database for weather docklet -- CSV loading and search.

Loads ~48K cities from a SimpleMaps CSV file. Provides prefix-based
search for the autocomplete entry in the docklet menu.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import NamedTuple

_CITIES_CSV = Path(__file__).parent.parent.parent / "assets" / "weather" / "cities.csv"


class CityEntry(NamedTuple):
    """A city with coordinates for weather lookup."""

    name: str  # ASCII city name (e.g. "Berlin")
    country: str  # Full country name (e.g. "Germany")
    display: str  # "Berlin, Germany" for UI
    lat: float
    lng: float


def load_cities(path: Path = _CITIES_CSV) -> list[CityEntry]:
    """Parse the cities CSV, returning entries sorted by population (largest first).

    Columns used: city_ascii, lat, lng, country, population.
    Rows with missing coordinates are skipped.
    """
    entries: list[tuple[int, CityEntry]] = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                lat = float(row["lat"])
                lng = float(row["lng"])
            except (ValueError, KeyError):
                continue
            name = row.get("city_ascii", "").strip()
            country = row.get("country", "").strip()
            if not name:
                continue
            pop_str = row.get("population", "").strip()
            pop = int(float(pop_str)) if pop_str else 0
            entry = CityEntry(
                name=name,
                country=country,
                display=f"{name}, {country}",
                lat=lat,
                lng=lng,
            )
            entries.append((pop, entry))

    # Sort by population descending so popular cities appear first in search
    entries.sort(key=lambda t: t[0], reverse=True)
    return [e for _, e in entries]


def search_cities(
    query: str, cities: list[CityEntry] | tuple[CityEntry, ...], limit: int = 10
) -> list[CityEntry]:
    """Case-insensitive prefix search on display name. Returns up to limit matches."""
    if not query:
        return []
    q = query.lower()
    results: list[CityEntry] = []
    for city in cities:
        if city.display.lower().startswith(q):
            results.append(city)
            if len(results) >= limit:
                break
    return results
