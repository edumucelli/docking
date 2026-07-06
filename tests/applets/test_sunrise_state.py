"""Tests for sunrise state helper functions."""

from __future__ import annotations

from docking.applets.sunrise.state import (
    LabelMode,
    cycle_active_index,
    normalize_label_mode,
    prefs_from_mapping,
    prefs_payload,
)


class TestNormalizeLabelMode:
    def test_returns_existing_mode(self):
        assert normalize_label_mode(LabelMode.NEXT_EVENT) == LabelMode.NEXT_EVENT

    def test_parses_string(self):
        assert normalize_label_mode("next_event") == LabelMode.NEXT_EVENT
        assert normalize_label_mode("phase") == LabelMode.PHASE
        assert normalize_label_mode("sunrise_sunset") == LabelMode.SUNRISE_SUNSET

    def test_invalid_falls_back(self):
        assert normalize_label_mode("invalid") == LabelMode.NEXT_EVENT
        assert normalize_label_mode(None) == LabelMode.NEXT_EVENT


class TestCycleActiveIndex:
    def test_single_item_returns_zero(self):
        assert cycle_active_index(count=1, current=0, direction_up=True) == 0
        assert cycle_active_index(count=1, current=0, direction_up=False) == 0
        assert cycle_active_index(count=0, current=5, direction_up=True) == 0

    def test_cycles_forward(self):
        assert cycle_active_index(count=3, current=0, direction_up=False) == 1
        assert cycle_active_index(count=3, current=2, direction_up=False) == 0

    def test_cycles_backward(self):
        assert cycle_active_index(count=3, current=0, direction_up=True) == 2
        assert cycle_active_index(count=3, current=2, direction_up=True) == 1


class TestPrefsFromMapping:
    def test_none_returns_defaults(self):
        prefs = prefs_from_mapping(None)
        assert prefs.cities == ()
        assert prefs.active_index == 0

    def test_empty_dict_returns_defaults(self):
        prefs = prefs_from_mapping({})
        assert prefs.cities == ()

    def test_parses_cities_list(self):
        prefs = prefs_from_mapping(
            {
                "cities": [
                    {"city_display": "London", "lat": 51.5, "lng": -0.1},
                    {"city_display": "Tokyo", "lat": 35.7, "lng": 139.7},
                ],
                "active_index": 1,
                "label_mode": "phase",
            }
        )
        assert len(prefs.cities) == 2
        assert prefs.cities[0].city_display == "London"
        assert prefs.cities[1].city_display == "Tokyo"
        assert prefs.active_index == 1
        assert prefs.label_mode == LabelMode.PHASE

    def test_parses_legacy_single_city(self):
        prefs = prefs_from_mapping(
            {
                "city_display": "Paris",
                "lat": 48.9,
                "lng": 2.3,
            }
        )
        assert len(prefs.cities) == 1
        assert prefs.cities[0].city_display == "Paris"

    def test_ignores_non_mapping_city_entries(self):
        prefs = prefs_from_mapping(
            {
                "cities": [
                    {"city_display": "Valid", "lat": 0, "lng": 0},
                    "not-a-dict",
                ],
            }
        )
        assert len(prefs.cities) == 1


class TestPrefsPayload:
    def test_builds_payload_with_cities(self):
        payload = prefs_payload(
            cities=(),
            active_index=0,
            label_mode=LabelMode.NEXT_EVENT,
        )
        assert payload["cities"] == []
        assert payload["active_index"] == 0
        assert payload["label_mode"] == "next_event"
