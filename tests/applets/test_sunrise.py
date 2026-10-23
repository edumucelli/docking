"""Tests for the Sunrise applet."""

from __future__ import annotations

import datetime as dt

from docking.applets.sunrise.applet import SunriseApplet
from docking.applets.sunrise.state import (
    CityPref,
    LabelMode,
    SolarPhase,
    build_snapshot,
    cycle_active_index,
    find_next_event,
    icon_label,
    phase_at,
    prefs_from_mapping,
    prefs_payload,
    solar_day,
    tooltip_text,
)
from docking.core.config import Config

_UTC = dt.timezone.utc
_BERLIN = CityPref(city_display="Berlin, Germany", lat=52.52, lng=13.41)
_TOKYO = CityPref(city_display="Tokyo, Japan", lat=35.68, lng=139.69)


class TestSunriseState:
    def test_prefs_from_mapping_new_format(self):
        prefs = prefs_from_mapping(
            {
                "cities": [
                    {"city_display": "Berlin", "lat": 52.52, "lng": 13.41},
                    {"city_display": "Tokyo", "lat": 35.68, "lng": 139.69},
                ],
                "active_index": 9,
                "label_mode": "phase",
            }
        )

        assert prefs.cities == (
            CityPref(city_display="Berlin", lat=52.52, lng=13.41),
            CityPref(city_display="Tokyo", lat=35.68, lng=139.69),
        )
        assert prefs.active_index == 1
        assert prefs.label_mode == LabelMode.PHASE

    def test_prefs_from_mapping_old_format(self):
        prefs = prefs_from_mapping({"city_display": "Paris", "lat": 48.85, "lng": 2.35})

        assert prefs.cities == (CityPref(city_display="Paris", lat=48.85, lng=2.35),)

    def test_prefs_payload(self):
        payload = prefs_payload(
            cities=(_BERLIN,),
            active_index=0,
            label_mode=LabelMode.SUNRISE_SUNSET,
        )

        assert payload["cities"] == [
            {"city_display": "Berlin, Germany", "lat": 52.52, "lng": 13.41}
        ]
        assert payload["label_mode"] == "sunrise_sunset"

    def test_cycle_active_index(self):
        assert cycle_active_index(count=3, current=0, direction_up=False) == 1
        assert cycle_active_index(count=3, current=0, direction_up=True) == 2
        assert cycle_active_index(count=1, current=0, direction_up=True) == 0

    def test_solar_day_orders_events(self):
        day = solar_day(
            date=dt.date(2026, 3, 20),
            latitude=0.0,
            longitude=0.0,
            tz=_UTC,
        )

        event_times = [event.when for event in day.events if event.when is not None]
        assert event_times == sorted(event_times)
        sunrise = day.event("sunrise")
        sunset = day.event("sunset")
        assert sunrise is not None
        assert sunset is not None
        assert sunrise.hour == 6
        assert sunset.hour == 18

    def test_phase_at_daylight_and_night(self):
        day = solar_day(
            date=dt.date(2026, 3, 20),
            latitude=0.0,
            longitude=0.0,
            tz=_UTC,
        )

        assert (
            phase_at(now=dt.datetime(2026, 3, 20, 12, tzinfo=_UTC), day=day)
            == SolarPhase.DAYLIGHT
        )
        assert (
            phase_at(now=dt.datetime(2026, 3, 20, 1, tzinfo=_UTC), day=day)
            == SolarPhase.NIGHT
        )

    def test_find_next_event(self):
        event = find_next_event(
            city=CityPref(city_display="Null Island", lat=0.0, lng=0.0),
            now=dt.datetime(2026, 3, 20, 5, 45, tzinfo=_UTC),
        )

        assert event is not None
        assert event.key == "sunrise"

    def test_polar_day(self):
        day = solar_day(
            date=dt.date(2026, 6, 21),
            latitude=78.22,
            longitude=15.65,
            tz=_UTC,
        )

        assert day.polar_day
        assert phase_at(now=dt.datetime(2026, 6, 21, 12, tzinfo=_UTC), day=day) == (
            SolarPhase.DAYLIGHT
        )

    def test_build_snapshot_without_city(self):
        snapshot = build_snapshot(
            city=None,
            now=dt.datetime(2026, 3, 20, 12, tzinfo=_UTC),
        )

        assert snapshot.today is None
        assert snapshot.next_event is None
        assert "no city" in tooltip_text(snapshot).lower()

    def test_icon_label_modes(self):
        snapshot = build_snapshot(
            city=CityPref(city_display="Null Island", lat=0.0, lng=0.0),
            now=dt.datetime(2026, 3, 20, 5, 45, tzinfo=_UTC),
            label_mode=LabelMode.NEXT_EVENT,
        )
        assert icon_label(snapshot).endswith("m")

        phase_snapshot = build_snapshot(
            city=CityPref(city_display="Null Island", lat=0.0, lng=0.0),
            now=dt.datetime(2026, 3, 20, 12, tzinfo=_UTC),
            label_mode=LabelMode.PHASE,
        )
        assert icon_label(phase_snapshot) == "Day"


class TestSunriseApplet:
    def test_creates_with_icon(self):
        applet = SunriseApplet(48, config=Config())
        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = SunriseApplet(size, config=Config())
            pixbuf = applet.create_icon(size=size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_add_city_saves_preferences(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = SunriseApplet(48, config=config)

        applet._add_city("Berlin, Germany", 52.52, 13.41)

        prefs = Config.load(path).applet_prefs["sunrise"]
        assert prefs["cities"] == [
            {"city_display": "Berlin, Germany", "lat": 52.52, "lng": 13.41}
        ]

    def test_scroll_cycles_city(self):
        applet = SunriseApplet(48, config=Config())
        applet._cities = [_BERLIN, _TOKYO]
        applet._active_index = 0

        applet.on_scroll(direction_up=False)

        assert applet._active_city == _TOKYO
