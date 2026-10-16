"""Malformed persisted data must not prevent applets from loading valid records."""

import math

import pytest

from docking.applets.aiusage.state import state_from_prefs as usage_state
from docking.applets.certwatch.state import prefs_from_mapping as cert_prefs
from docking.applets.hydration.state import DEFAULT_INTERVAL
from docking.applets.hydration.state import state_from_prefs as hydration_state
from docking.applets.sunrise.state import prefs_from_mapping as sunrise_prefs
from docking.applets.weather.state import prefs_from_mapping as weather_prefs


@pytest.mark.parametrize(
    "value", [None, "invalid", [], {}, False, 0, -10, float("inf"), float("nan")]
)
def test_invalid_hydration_interval_uses_default(value):
    assert hydration_state({"interval": value}).interval_min == DEFAULT_INTERVAL


@pytest.mark.parametrize("loader", [weather_prefs, sunrise_prefs])
@pytest.mark.parametrize(
    "field,value",
    [
        ("lat", None),
        ("lat", []),
        ("lat", "invalid"),
        ("lat", float("nan")),
        ("lat", float("inf")),
        ("lat", 91),
        ("lng", -181),
        ("lng", {}),
    ],
)
def test_invalid_city_does_not_discard_other_saved_cities(loader, field, value):
    bad = {"city_display": "Broken", "lat": 1, "lng": 1, field: value}
    valid = {"city_display": "Berlin", "lat": 52.52, "lng": 13.41}
    prefs = loader({"cities": [bad, valid]})
    assert len(prefs.cities) == 1
    assert prefs.cities[0].city_display == "Berlin"


@pytest.mark.parametrize("loader", [weather_prefs, sunrise_prefs])
@pytest.mark.parametrize("value", [None, "bad", [], float("inf"), float("nan")])
def test_invalid_city_index_uses_first_valid_city(loader, value):
    prefs = loader(
        {
            "cities": [{"city_display": "Berlin", "lat": 52.52, "lng": 13.41}],
            "active_index": value,
        }
    )
    assert prefs.active_index == 0


@pytest.mark.parametrize("value", [None, False, 0, "bad", {}])
def test_weather_rejects_non_list_city_storage(value):
    assert weather_prefs({"cities": value}).cities == ()


@pytest.mark.parametrize("loader", [weather_prefs, sunrise_prefs])
def test_invalid_legacy_city_is_ignored(loader):
    assert loader({"city_display": "Broken", "lat": None, "lng": "bad"}).cities == ()


@pytest.mark.parametrize("field", ["in", "out", "cw", "cr", "pc"])
@pytest.mark.parametrize("value", [None, "bad", [], {}, -1, float("inf"), float("nan")])
def test_invalid_usage_counter_is_zero_without_losing_valid_counter(field, value):
    raw = {"in": 100, "out": 20, "cw": 3, "cr": 4, "pc": 1.25, field: value}
    state = usage_state(
        {"days": [{"date": "2026-10-16", "sessions": 1, "by_model": {"gpt-5": raw}}]}
    )
    model = dict(state.days[0].by_model)["gpt-5"]
    attribute = {
        "in": "input_tokens",
        "out": "output_tokens",
        "cw": "cache_write_tokens",
        "cr": "cache_read_tokens",
        "pc": "precalculated_cost",
    }[field]
    assert getattr(model, attribute) == 0
    if field != "in":
        assert model.input_tokens == 100
    assert math.isfinite(model.precalculated_cost)


@pytest.mark.parametrize("value", [None, [], "bad"])
def test_invalid_usage_model_container_is_ignored(value):
    state = usage_state({"days": [{"date": "2026-10-16", "by_model": value}]})
    assert state.days[0].by_model == ()


@pytest.mark.parametrize("value", [None, "bad", [], {}, float("inf"), float("nan"), -1])
def test_invalid_usage_session_count_is_zero(value):
    state = usage_state(
        {"days": [{"date": "2026-10-16", "sessions": value, "by_model": {}}]}
    )
    assert state.days[0].sessions == 0


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan"), True])
def test_invalid_certificate_port_uses_https_default(value):
    prefs = cert_prefs({"domains": [{"host": "example.test", "port": value}]})
    assert prefs.domains[0].port == 443


@pytest.mark.parametrize("name", ["weather", "sunrise"])
@pytest.mark.parametrize("invalid", [None, {"city_display": "Invalid", "lat": None}])
def test_filtering_bad_cities_keeps_the_saved_selection(name, invalid):
    from importlib import import_module

    loader = import_module(f"docking.applets.{name}.state").prefs_from_mapping
    data = {
        "cities": [
            invalid,
            {"city_display": "Berlin", "lat": 52.52, "lng": 13.41},
            {"city_display": "Tokyo", "lat": 35.68, "lng": 139.69},
        ],
        "active_index": 1,
    }
    prefs = loader(data)
    assert prefs.cities[prefs.active_index].city_display == "Berlin"
