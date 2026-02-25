"""Tests for weather city database loading and search."""

import csv
import pytest

from docking.docklets.weather.cities import CityEntry, load_cities, search_cities


@pytest.fixture
def sample_csv(tmp_path):
    """Create a small cities CSV for testing."""
    path = tmp_path / "cities.csv"
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "city",
                "city_ascii",
                "lat",
                "lng",
                "country",
                "iso2",
                "iso3",
                "admin_name",
                "capital",
                "population",
                "id",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "city": "Berlin",
                "city_ascii": "Berlin",
                "lat": "52.52",
                "lng": "13.41",
                "country": "Germany",
                "iso2": "DE",
                "iso3": "DEU",
                "admin_name": "Berlin",
                "capital": "primary",
                "population": "3748148",
                "id": "1",
            }
        )
        writer.writerow(
            {
                "city": "Bern",
                "city_ascii": "Bern",
                "lat": "46.95",
                "lng": "7.45",
                "country": "Switzerland",
                "iso2": "CH",
                "iso3": "CHE",
                "admin_name": "Bern",
                "capital": "primary",
                "population": "422000",
                "id": "2",
            }
        )
        writer.writerow(
            {
                "city": "Tokyo",
                "city_ascii": "Tokyo",
                "lat": "35.69",
                "lng": "139.75",
                "country": "Japan",
                "iso2": "JP",
                "iso3": "JPN",
                "admin_name": "Tokyo",
                "capital": "primary",
                "population": "13960000",
                "id": "3",
            }
        )
    return path


class TestLoadCities:
    def test_parses_csv(self, sample_csv):
        cities = load_cities(sample_csv)
        assert len(cities) == 3

    def test_sorted_by_population_desc(self, sample_csv):
        cities = load_cities(sample_csv)
        # Tokyo (13.9M) > Berlin (3.7M) > Bern (422K)
        assert cities[0].name == "Tokyo"
        assert cities[1].name == "Berlin"
        assert cities[2].name == "Bern"

    def test_display_format(self, sample_csv):
        cities = load_cities(sample_csv)
        berlin = next(c for c in cities if c.name == "Berlin")
        assert berlin.display == "Berlin, Germany"

    def test_coordinates(self, sample_csv):
        cities = load_cities(sample_csv)
        berlin = next(c for c in cities if c.name == "Berlin")
        assert berlin.lat == pytest.approx(52.52)
        assert berlin.lng == pytest.approx(13.41)

    def test_skips_invalid_rows(self, tmp_path):
        path = tmp_path / "bad.csv"
        with open(path, "w") as f:
            f.write(
                "city,city_ascii,lat,lng,country,iso2,iso3,admin_name,capital,population,id\n"
            )
            f.write("Bad,,invalid,invalid,X,X,X,X,X,,1\n")
        cities = load_cities(path)
        assert len(cities) == 0


class TestSearchCities:
    def test_prefix_match(self, sample_csv):
        cities = load_cities(sample_csv)
        results = search_cities("Ber", cities)
        names = [c.name for c in results]
        assert "Berlin" in names
        assert "Bern" in names

    def test_case_insensitive(self, sample_csv):
        cities = load_cities(sample_csv)
        results = search_cities("ber", cities)
        assert len(results) == 2

    def test_empty_query_returns_empty(self, sample_csv):
        cities = load_cities(sample_csv)
        assert search_cities("", cities) == []

    def test_no_match(self, sample_csv):
        cities = load_cities(sample_csv)
        assert search_cities("xyz", cities) == []

    def test_respects_limit(self, sample_csv):
        cities = load_cities(sample_csv)
        results = search_cities("B", cities, limit=1)
        assert len(results) == 1

    def test_matches_display_name(self, sample_csv):
        cities = load_cities(sample_csv)
        # Search by "Berlin, G" should match
        results = search_cities("Berlin, G", cities)
        assert len(results) == 1
        assert results[0].name == "Berlin"
