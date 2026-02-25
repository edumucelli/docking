"""Tests for weather city database loading and search."""

import csv
import gzip

import pytest

from docking.applets.weather.cities import CityEntry, load_cities, search_cities

_FIELDS = ["city_ascii", "lat", "lng", "country", "population"]


@pytest.fixture
def sample_gz(tmp_path):
    """Create a small gzipped cities CSV for testing."""
    path = tmp_path / "cities.csv.gz"
    with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        writer.writeheader()
        writer.writerow(
            {
                "city_ascii": "Berlin",
                "lat": "52.52",
                "lng": "13.41",
                "country": "Germany",
                "population": "3748148",
            }
        )
        writer.writerow(
            {
                "city_ascii": "Bern",
                "lat": "46.95",
                "lng": "7.45",
                "country": "Switzerland",
                "population": "422000",
            }
        )
        writer.writerow(
            {
                "city_ascii": "Tokyo",
                "lat": "35.69",
                "lng": "139.75",
                "country": "Japan",
                "population": "13960000",
            }
        )
    return path


class TestLoadCities:
    def test_parses_csv(self, sample_gz):
        cities = load_cities(sample_gz)
        assert len(cities) == 3

    def test_sorted_by_population_desc(self, sample_gz):
        cities = load_cities(sample_gz)
        # Tokyo (13.9M) > Berlin (3.7M) > Bern (422K)
        assert cities[0].name == "Tokyo"
        assert cities[1].name == "Berlin"
        assert cities[2].name == "Bern"

    def test_display_format(self, sample_gz):
        cities = load_cities(sample_gz)
        berlin = next(c for c in cities if c.name == "Berlin")
        assert berlin.display == "Berlin, Germany"

    def test_coordinates(self, sample_gz):
        cities = load_cities(sample_gz)
        berlin = next(c for c in cities if c.name == "Berlin")
        assert berlin.lat == pytest.approx(52.52)
        assert berlin.lng == pytest.approx(13.41)

    def test_skips_invalid_rows(self, tmp_path):
        path = tmp_path / "bad.csv.gz"
        with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
            f.write("city_ascii,lat,lng,country,population\n")
            f.write(",invalid,invalid,X,\n")
        cities = load_cities(path)
        assert len(cities) == 0


class TestSearchCities:
    def test_prefix_match(self, sample_gz):
        cities = load_cities(sample_gz)
        results = search_cities("Ber", cities)
        names = [c.name for c in results]
        assert "Berlin" in names
        assert "Bern" in names

    def test_case_insensitive(self, sample_gz):
        cities = load_cities(sample_gz)
        results = search_cities("ber", cities)
        assert len(results) == 2

    def test_empty_query_returns_empty(self, sample_gz):
        cities = load_cities(sample_gz)
        assert search_cities("", cities) == []

    def test_no_match(self, sample_gz):
        cities = load_cities(sample_gz)
        assert search_cities("xyz", cities) == []

    def test_respects_limit(self, sample_gz):
        cities = load_cities(sample_gz)
        results = search_cities("B", cities, limit=1)
        assert len(results) == 1

    def test_matches_display_name(self, sample_gz):
        cities = load_cities(sample_gz)
        results = search_cities("Berlin, G", cities)
        assert len(results) == 1
        assert results[0].name == "Berlin"
