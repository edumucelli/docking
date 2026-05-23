"""Tests for the weather applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

import docking.applets.weather.applet as weather_applet_mod
from docking.applets.weather.api import (
    AirQualityData,
    DailyForecast,
    WeatherData,
    aqi_label,
)
from docking.applets.weather.applet import WeatherApplet
from docking.applets.weather.state import (
    CityPref,
    TemperatureUnit,
    WeatherPrefs,
    cycle_active_index,
    format_temperature,
    format_temperature_compact,
    format_temperature_range,
    prefs_from_mapping,
    prefs_payload,
)
from docking.core.config import Config

_SAMPLE_WEATHER = WeatherData(
    temperature=22.0,
    weather_code=0,
    description="Clear sky",
    icon_name="weather-clear",
    daily=[
        DailyForecast("Mon", 0, "Clear sky", 25.0, 18.0),
        DailyForecast("Tue", 61, "Slight rain", 20.0, 15.0),
    ],
)

_BERLIN = CityPref(city_display="Berlin, Germany", lat=52.52, lng=13.41)
_TOKYO = CityPref(city_display="Tokyo, Japan", lat=35.68, lng=139.69)
_LONDON = CityPref(city_display="London, United Kingdom", lat=51.51, lng=-0.13)


class _ImmediateWorker:
    def __init__(self, **_kwargs) -> None:
        pass

    def run(self, *, fn, on_result=None, on_error=None, **_kwargs) -> None:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)


def _make_applet(icon_size: int = 48, *, config: Config | None = None) -> WeatherApplet:
    with patch("docking.applets.weather.applet.BackgroundWorker", _ImmediateWorker):
        return WeatherApplet(icon_size, config=config)


# -- State-level tests -------------------------------------------------------


class TestCycleActiveIndex:
    def test_forward_wraps(self):
        assert cycle_active_index(count=3, current=2, direction_up=False) == 0

    def test_backward_wraps(self):
        assert cycle_active_index(count=3, current=0, direction_up=True) == 2

    def test_forward(self):
        assert cycle_active_index(count=3, current=0, direction_up=False) == 1

    def test_backward(self):
        assert cycle_active_index(count=3, current=2, direction_up=True) == 1

    def test_single_city_noop(self):
        assert cycle_active_index(count=1, current=0, direction_up=True) == 0
        assert cycle_active_index(count=1, current=0, direction_up=False) == 0

    def test_empty_noop(self):
        assert cycle_active_index(count=0, current=0, direction_up=True) == 0


class TestPrefsFromMapping:
    def test_new_format(self):
        raw = {
            "cities": [
                {"city_display": "Berlin", "lat": 52.52, "lng": 13.41},
                {"city_display": "Tokyo", "lat": 35.68, "lng": 139.69},
            ],
            "active_index": 1,
            "show_temperature": False,
            "temperature_unit": "fahrenheit",
        }
        prefs = prefs_from_mapping(raw)
        assert len(prefs.cities) == 2
        assert prefs.cities[0].city_display == "Berlin"
        assert prefs.active_index == 1
        assert prefs.show_temperature is False
        assert prefs.temperature_unit == TemperatureUnit.FAHRENHEIT

    def test_old_scalar_format_migrates(self):
        raw = {
            "city_display": "Paris",
            "lat": 48.85,
            "lng": 2.35,
            "show_temperature": True,
        }
        prefs = prefs_from_mapping(raw)
        assert len(prefs.cities) == 1
        assert prefs.cities[0].city_display == "Paris"
        assert prefs.cities[0].lat == 48.85
        assert prefs.active_index == 0

    def test_old_empty_city_migrates_to_empty(self):
        raw = {"city_display": "", "lat": 0.0, "lng": 0.0}
        prefs = prefs_from_mapping(raw)
        assert len(prefs.cities) == 0

    def test_none_returns_defaults(self):
        prefs = prefs_from_mapping(None)
        assert prefs == WeatherPrefs()

    def test_clamps_active_index(self):
        raw = {
            "cities": [{"city_display": "A", "lat": 0, "lng": 0}],
            "active_index": 99,
        }
        prefs = prefs_from_mapping(raw)
        assert prefs.active_index == 0


class TestPrefsPayload:
    def test_round_trips(self):
        cities = (_BERLIN, _TOKYO)
        payload = prefs_payload(
            cities=cities,
            active_index=1,
            show_temperature=False,
            temperature_unit=TemperatureUnit.FAHRENHEIT,
        )
        prefs = prefs_from_mapping(payload)
        assert prefs.cities == cities
        assert prefs.active_index == 1
        assert prefs.show_temperature is False
        assert prefs.temperature_unit == TemperatureUnit.FAHRENHEIT

    def test_temperature_formatting(self):
        assert format_temperature(22.0) == "22\N{DEGREE SIGN}C"
        assert (
            format_temperature(22.0, temperature_unit=TemperatureUnit.FAHRENHEIT)
            == "72\N{DEGREE SIGN}F"
        )
        assert (
            format_temperature_compact(
                22.0,
                temperature_unit=TemperatureUnit.FAHRENHEIT,
            )
            == "72\N{DEGREE SIGN}"
        )
        assert (
            format_temperature_range(
                low_celsius=18.0,
                high_celsius=25.0,
                temperature_unit=TemperatureUnit.FAHRENHEIT,
            )
            == "64/77\N{DEGREE SIGN}F"
        )


# -- Applet-level tests -------------------------------------------------------


class TestWeatherAppletCreation:
    def test_creates_with_default_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_default_tooltip_no_city(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "no city" in applet.item.name.lower()

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = _make_applet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None


class TestWeatherTooltip:
    def test_tooltip_shows_city_and_temp(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        applet.refresh_tooltip()
        assert "Berlin" in applet.item.name
        assert "22" in applet.item.name

    def test_tooltip_discloses_update_cadence(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        applet.refresh_tooltip()

        assert "Updates every 5 minutes" in applet.item.name

    def test_tooltip_uses_selected_temperature_unit(self):
        config = Config(applet_prefs={"weather": {"temperature_unit": "fahrenheit"}})
        applet = _make_applet(config=config)
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        applet.refresh_tooltip()
        assert "72\N{DEGREE SIGN}F" in applet.item.name
        assert "64/77\N{DEGREE SIGN}F" in applet.item.name

    def test_tooltip_includes_daily_forecast(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        applet.refresh_tooltip()
        assert "Mon" in applet.item.name

    def test_tooltip_loading_state(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = None
        applet._loading = True
        applet.refresh_tooltip()
        assert "loading" in applet.item.name.lower()

    def test_tooltip_no_data_state(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = None
        applet.refresh_tooltip()
        assert "no data yet" in applet.item.name.lower()

    def test_tooltip_unavailable_state_after_failed_fetch(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = None
        applet._fetch_failed = True
        applet.refresh_tooltip()
        assert "unavailable" in applet.item.name.lower()


class TestWeatherTemperatureOverlay:
    def test_overlay_renders_with_weather_data(self):
        applet = _make_applet()
        applet._weather = _SAMPLE_WEATHER
        applet._show_temperature = True
        pixbuf = applet.create_icon(48)
        assert pixbuf is not None

    def test_no_overlay_when_disabled(self):
        applet = _make_applet()
        applet._weather = _SAMPLE_WEATHER
        applet._show_temperature = False
        pixbuf = applet.create_icon(48)
        assert pixbuf is not None

    def test_no_overlay_without_weather_data(self):
        applet = _make_applet()
        applet._weather = None
        applet._show_temperature = True
        pixbuf = applet.create_icon(48)
        assert pixbuf is not None


class TestWeatherMenu:
    def test_menu_has_show_temp_and_unit_selector(self):
        applet = _make_applet()
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert "Show Temperature" in labels
        assert "Temperature Unit" in labels
        assert "Add City..." not in labels

    def test_menu_includes_city_header_when_set(self):
        applet = _make_applet()
        applet._cities = [_TOKYO]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        items = applet.get_menu_items()
        assert "Tokyo" in items[0].get_label()
        assert not items[0].get_sensitive()
        assert any(item.get_label() == "Updates every 5 minutes" for item in items)

    def test_menu_no_city_header_when_unset(self):
        applet = _make_applet()
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert "Show Temperature" in labels
        # No city header -> first item is show_temp
        assert items[0].get_label() == "Show Temperature"

    def test_menu_unit_selector_contains_celsius_and_fahrenheit(self):
        applet = _make_applet()
        unit_root = next(
            item
            for item in applet.get_menu_items()
            if item.get_label() == "Temperature Unit"
        )

        labels = [item.get_label() for item in unit_root.get_submenu().children]

        assert labels == ["Celsius", "Fahrenheit"]

    def test_menu_has_remove_when_multi_city(self):
        applet = _make_applet()
        applet._cities = [_BERLIN, _TOKYO]
        applet._active_index = 0
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert any("Remove" in label and "Berlin" in label for label in labels)

    def test_menu_no_remove_when_single_city(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert not any("Remove" in label for label in labels)


_SAMPLE_AQI = AirQualityData(
    aqi=28,
    pm2_5=8.1,
    pm10=9.1,
    label="Fair",
    uv_index=5.7,
)


class TestAqiLabel:
    def test_good(self):
        assert aqi_label(aqi=15) == "Good"

    def test_fair(self):
        assert aqi_label(aqi=30) == "Fair"

    def test_moderate(self):
        assert aqi_label(aqi=50) == "Moderate"

    def test_poor(self):
        assert aqi_label(aqi=70) == "Poor"

    def test_very_poor(self):
        assert aqi_label(aqi=90) == "Very Poor"

    def test_extremely_poor(self):
        assert aqi_label(aqi=150) == "Extremely Poor"

    def test_boundary_20(self):
        assert aqi_label(aqi=20) == "Good"

    def test_boundary_21(self):
        assert aqi_label(aqi=21) == "Fair"


class TestAirQualityInTooltip:
    def test_tooltip_includes_aqi_when_available(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = _SAMPLE_AQI
        applet.refresh_tooltip()
        assert "Air: Fair" in applet.item.name
        assert "UV Index: 5.7" in applet.item.name

    def test_tooltip_no_aqi_when_unavailable(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = None
        applet.refresh_tooltip()
        assert "Air:" not in applet.item.name


class TestWeatherPrefs:
    def test_loads_city_from_config_old_format(self):
        # Given - old scalar prefs format
        config = Config(
            applet_prefs={
                "weather": {
                    "city_display": "Paris, France",
                    "lat": 48.85,
                    "lng": 2.35,
                    "show_temperature": False,
                }
            }
        )
        # When
        applet = _make_applet(config=config)
        # Then - migrated to cities list
        assert len(applet._cities) == 1
        assert applet._cities[0].city_display == "Paris, France"
        assert applet._cities[0].lat == 48.85
        assert applet._show_temperature is False

    def test_loads_city_from_config_new_format(self):
        config = Config(
            applet_prefs={
                "weather": {
                    "cities": [
                        {"city_display": "Berlin", "lat": 52.52, "lng": 13.41},
                        {"city_display": "Tokyo", "lat": 35.68, "lng": 139.69},
                    ],
                    "active_index": 1,
                    "show_temperature": True,
                    "temperature_unit": "fahrenheit",
                }
            }
        )
        applet = _make_applet(config=config)
        assert len(applet._cities) == 2
        assert applet._active_index == 1
        assert applet._temperature_unit == TemperatureUnit.FAHRENHEIT

    def test_saves_prefs_on_add_city(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = _make_applet(config=config)

        applet._add_city("London, United Kingdom", 51.51, -0.13)

        reloaded = Config.load(path)
        prefs = reloaded.applet_prefs["weather"]
        assert len(prefs["cities"]) == 1
        assert prefs["cities"][0]["city_display"] == "London, United Kingdom"

    def test_saves_show_temperature_pref(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = _make_applet(config=config)

        applet._show_temperature = False
        applet._save_prefs()

        reloaded = Config.load(path)
        assert reloaded.applet_prefs["weather"]["show_temperature"] is False

    def test_saves_temperature_unit_pref(self):
        config = Config(applet_prefs={})
        applet = _make_applet(config=config)
        item = Gtk.RadioMenuItem(label="Fahrenheit")
        item.set_active(True)

        applet._on_temperature_unit_selected(
            widget=item,
            temperature_unit=TemperatureUnit.FAHRENHEIT,
        )

        assert config.applet_prefs["weather"]["temperature_unit"] == "fahrenheit"


class TestWeatherAsyncFetch:
    def test_on_fetch_result_ignores_stale_request(self, monkeypatch):
        applet = _make_applet()
        applet._fetch_request_id = 2
        applet._weather = None
        applet._air_quality = None
        refresh = MagicMock()
        monkeypatch.setattr(applet, "present", refresh)
        result = applet._on_fetch_result(1, _SAMPLE_WEATHER, _SAMPLE_AQI)
        assert result is False
        assert applet._weather is None
        refresh.assert_not_called()

    def test_on_fetch_result_applies_latest_request(self, monkeypatch):
        applet = _make_applet()
        applet._fetch_request_id = 3
        refresh = MagicMock()
        monkeypatch.setattr(applet, "present", refresh)
        result = applet._on_fetch_result(3, _SAMPLE_WEATHER, _SAMPLE_AQI)
        assert result is False
        assert applet._weather == _SAMPLE_WEATHER
        assert applet._air_quality == _SAMPLE_AQI
        refresh.assert_called_once()

    def test_on_fetch_result_marks_failed_when_weather_missing(self, monkeypatch):
        applet = _make_applet()
        applet._fetch_request_id = 4
        refresh = MagicMock()
        monkeypatch.setattr(applet, "present", refresh)

        result = applet._on_fetch_result(4, None, None)

        assert result is False
        assert applet._fetch_failed is True
        refresh.assert_called_once()

    def test_on_fetch_error_marks_failed(self, monkeypatch):
        applet = _make_applet()
        applet._fetch_request_id = 5
        refresh = MagicMock()
        monkeypatch.setattr(applet, "present", refresh)

        result = applet._on_fetch_error(request_id=5, exc=RuntimeError("boom"))

        assert result is False
        assert applet._weather is None
        assert applet._air_quality is None
        assert applet._fetch_failed is True
        assert applet._fetch_error == "boom"
        refresh.assert_called_once()

    def test_fetch_error_keeps_previous_weather(self):
        applet = _make_applet()
        applet._fetch_request_id = 5
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = _SAMPLE_AQI

        assert applet._on_fetch_error(request_id=5, exc=RuntimeError("boom")) is False

        assert applet._weather == _SAMPLE_WEATHER
        assert applet._air_quality == _SAMPLE_AQI
        assert applet._fetch_failed is True

    def test_fetch_async_uses_active_city_coords(self, monkeypatch):
        applet = _make_applet()
        applet._cities = [_BERLIN, _TOKYO]
        applet._active_index = 1

        captured = {}

        def fake_fetch_weather(lat, lng):
            captured["lat"] = lat
            captured["lng"] = lng
            return _SAMPLE_WEATHER

        monkeypatch.setattr(weather_applet_mod, "fetch_weather", fake_fetch_weather)
        monkeypatch.setattr(
            weather_applet_mod,
            "fetch_air_quality",
            MagicMock(return_value=_SAMPLE_AQI),
        )
        applet._fetch_async()
        assert captured["lat"] == _TOKYO.lat
        assert captured["lng"] == _TOKYO.lng


class TestWeatherLifecycleAndInteractions:
    def test_start_schedules_poll_and_delayed_startup_fetch_when_city_selected(
        self,
        monkeypatch,
    ):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        timer_ids = iter([99, 101])

        def fake_timeout_add_seconds(_interval, _callback):
            return next(timer_ids)

        monkeypatch.setattr(
            weather_applet_mod.GLib,
            "timeout_add_seconds",
            fake_timeout_add_seconds,
        )
        applet.start(notify=lambda: None)
        assert applet._timer_id == 99
        assert applet._startup_fetch_timer_id == 101

    def test_run_startup_fetch_triggers_fetch_once_and_clears_timer(self, monkeypatch):
        applet = _make_applet()
        applet._startup_fetch_timer_id = 88
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        result = applet._run_startup_fetch()
        assert result is False
        assert applet._startup_fetch_timer_id == 0
        fetch.assert_called_once()

    def test_stop_removes_active_timer(self, monkeypatch):
        applet = _make_applet()
        applet._timer_id = 77
        applet._startup_fetch_timer_id = 78
        remove = MagicMock()
        monkeypatch.setattr(weather_applet_mod.GLib, "source_remove", remove)
        applet.stop()
        assert remove.call_args_list == [call(77), call(78)]
        assert applet._timer_id == 0
        assert applet._startup_fetch_timer_id == 0

    def test_tick_fetches_when_city_is_set(self, monkeypatch):
        applet = _make_applet()
        applet._cities = [_TOKYO]
        applet._active_index = 0
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        result = applet._tick()
        assert result is True
        fetch.assert_called_once()

    def test_on_toggle_temperature_saves_and_refreshes(self):
        applet = _make_applet()
        applet._save_prefs = MagicMock()
        applet.present = MagicMock()
        widget = MagicMock()
        widget.get_active.return_value = False
        applet._on_toggle_temperature(widget)
        assert applet._show_temperature is False
        applet._save_prefs.assert_called_once()
        applet.present.assert_called_once()

    def test_on_clicked_opens_city_dialog(self):
        applet = _make_applet()
        show = MagicMock()
        applet._show_city_dialog = show

        applet.on_clicked()

        show.assert_called_once()

    def test_start_without_city_does_not_fetch(self, monkeypatch):
        applet = _make_applet()
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        monkeypatch.setattr(
            weather_applet_mod.GLib, "timeout_add_seconds", lambda *_a: 51
        )
        applet.start(notify=lambda: None)
        assert applet._timer_id == 51
        assert applet._startup_fetch_timer_id == 0
        fetch.assert_not_called()

    def test_tick_without_city_does_not_fetch(self, monkeypatch):
        applet = _make_applet()
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        result = applet._tick()
        assert result is True
        fetch.assert_not_called()

    def test_fetch_async_cancels_pending_startup_timer(self, monkeypatch):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._startup_fetch_timer_id = 44
        remove = MagicMock()
        monkeypatch.setattr(weather_applet_mod.GLib, "source_remove", remove)
        monkeypatch.setattr(
            weather_applet_mod,
            "fetch_weather",
            MagicMock(return_value=_SAMPLE_WEATHER),
        )
        monkeypatch.setattr(
            weather_applet_mod,
            "fetch_air_quality",
            MagicMock(return_value=_SAMPLE_AQI),
        )
        applet._fetch_async()
        remove.assert_called_once_with(44)
        assert applet._startup_fetch_timer_id == 0


class TestMultiCity:
    def test_add_city(self):
        applet = _make_applet()
        applet.present = MagicMock()
        applet._add_city("Berlin", 52.52, 13.41)
        assert len(applet._cities) == 1
        assert applet._active_index == 0

    def test_add_second_city(self):
        applet = _make_applet()
        applet.present = MagicMock()
        applet._add_city("Berlin", 52.52, 13.41)
        applet._add_city("Tokyo", 35.68, 139.69)
        assert len(applet._cities) == 2
        assert applet._active_index == 1

    def test_add_duplicate_switches_instead(self):
        applet = _make_applet()
        applet.present = MagicMock()
        applet._cities = [_BERLIN, _TOKYO]
        applet._active_index = 1
        applet._add_city("Berlin", 52.52, 13.41)
        # Should switch to Berlin, not add a third
        assert len(applet._cities) == 2
        assert applet._active_index == 0

    def test_scroll_cycles_forward(self):
        applet = _make_applet()
        applet._cities = [_BERLIN, _TOKYO, _LONDON]
        applet._active_index = 0
        applet.present = MagicMock()
        applet.on_scroll(direction_up=False)
        assert applet._active_index == 1
        applet.on_scroll(direction_up=False)
        assert applet._active_index == 2
        applet.on_scroll(direction_up=False)
        assert applet._active_index == 0

    def test_scroll_cycles_backward(self):
        applet = _make_applet()
        applet._cities = [_BERLIN, _TOKYO, _LONDON]
        applet._active_index = 0
        applet.present = MagicMock()
        applet.on_scroll(direction_up=True)
        assert applet._active_index == 2

    def test_scroll_noop_single_city(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet.present = MagicMock()
        applet.on_scroll(direction_up=True)
        assert applet._active_index == 0
        applet.present.assert_not_called()

    def test_scroll_clears_weather_and_fetches(self, monkeypatch):
        applet = _make_applet()
        applet._cities = [_BERLIN, _TOKYO]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = _SAMPLE_AQI
        applet.present = MagicMock()
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        applet.on_scroll(direction_up=False)
        assert applet._weather is None
        assert applet._air_quality is None
        fetch.assert_called_once()

    def test_remove_active_city(self):
        applet = _make_applet()
        applet._cities = [_BERLIN, _TOKYO, _LONDON]
        applet._active_index = 1
        applet.present = MagicMock()
        applet._remove_active_city()
        assert len(applet._cities) == 2
        assert applet._cities[0] == _BERLIN
        assert applet._cities[1] == _LONDON
        assert applet._active_index == 1

    def test_remove_last_index_clamps(self):
        applet = _make_applet()
        applet._cities = [_BERLIN, _TOKYO]
        applet._active_index = 1
        applet.present = MagicMock()
        applet._remove_active_city()
        assert len(applet._cities) == 1
        assert applet._active_index == 0

    def test_remove_noop_single_city(self):
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._remove_active_city()
        assert len(applet._cities) == 1

    def test_active_city_property(self):
        applet = _make_applet()
        assert applet._active_city is None
        applet._cities = [_BERLIN, _TOKYO]
        applet._active_index = 1
        assert applet._active_city == _TOKYO


class _FakeWeatherBox:
    def __init__(self, orientation=None, spacing=0):
        self.orientation = orientation
        self.spacing = spacing
        self.children: list[object] = []
        self.margins: dict[str, int] = {}

    def set_spacing(self, value: int) -> None:
        self.spacing = value

    def set_margin_start(self, value: int) -> None:
        self.margins["start"] = value

    def set_margin_end(self, value: int) -> None:
        self.margins["end"] = value

    def set_margin_top(self, value: int) -> None:
        self.margins["top"] = value

    def set_margin_bottom(self, value: int) -> None:
        self.margins["bottom"] = value

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)


class _FakeWeatherEntry:
    def __init__(self):
        self._text = ""
        self._completion = None
        self.callbacks: dict[str, object] = {}

    def set_placeholder_text(self, _text: str) -> None:
        return

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def set_completion(self, completion) -> None:
        self._completion = completion

    def set_text(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text

    def grab_focus(self) -> None:
        return


class _FakeWeatherCompletion:
    def __init__(self):
        self.model = None
        self.callbacks: dict[str, object] = {}

    def set_model(self, model) -> None:
        self.model = model

    def set_text_column(self, _index: int) -> None:
        return

    def set_minimum_key_length(self, _length: int) -> None:
        return

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback


class _FakeWeatherStore:
    def __init__(self, *_types):
        self.rows: list[list[object]] = []

    def clear(self) -> None:
        self.rows.clear()

    def append(self, row: list[object]) -> None:
        self.rows.append(row)

    def get_value(self, tree_iter: int, column: int):
        return self.rows[tree_iter][column]


class _FakeWeatherDialog:
    def __init__(self, **_kwargs):
        self.destroy = MagicMock()
        self._content = _FakeWeatherBox()
        self.buttons: list[tuple[object, object]] = []
        self.callbacks: dict[str, object] = {}

    def set_default_size(self, *_args) -> None:
        return

    def set_position(self, *_args) -> None:
        return

    def add_button(self, label, response) -> None:
        self.buttons.append((label, response))

    def connect(self, signal: str, callback) -> None:
        self.callbacks[signal] = callback

    def get_content_area(self):
        return self._content

    def show_all(self) -> None:
        return


class TestWeatherDialogAndWidget:
    def test_show_city_dialog_search_and_select(self, monkeypatch):
        # Given
        applet = _make_applet()
        created_entry = _FakeWeatherEntry()
        created_completion = _FakeWeatherCompletion()
        created_dialog = _FakeWeatherDialog()
        city = SimpleNamespace(display="Berlin, Germany", lat=52.52, lng=13.41)
        monkeypatch.setattr(weather_applet_mod, "cached_cities", lambda: [city])
        monkeypatch.setattr(
            weather_applet_mod, "search_cities", lambda *_a, **_k: [city]
        )
        monkeypatch.setattr(
            weather_applet_mod,
            "Gtk",
            SimpleNamespace(
                Dialog=lambda **_kwargs: created_dialog,
                DialogFlags=SimpleNamespace(MODAL=1, DESTROY_WITH_PARENT=2),
                ResponseType=SimpleNamespace(CANCEL=0),
                WindowPosition=SimpleNamespace(MOUSE=1),
                Entry=lambda: created_entry,
                EntryCompletion=lambda: created_completion,
                ListStore=lambda *_types: _FakeWeatherStore(),
                CheckMenuItem=weather_applet_mod.Gtk.CheckMenuItem,
                MenuItem=weather_applet_mod.Gtk.MenuItem,
                Orientation=weather_applet_mod.Gtk.Orientation,
                Box=weather_applet_mod.Gtk.Box,
                Image=weather_applet_mod.Gtk.Image,
                IconSize=weather_applet_mod.Gtk.IconSize,
                Label=weather_applet_mod.Gtk.Label,
            ),
        )
        add_city = MagicMock()
        monkeypatch.setattr(applet, "_add_city", add_city)
        monkeypatch.setattr(weather_applet_mod.GLib, "idle_add", lambda fn: fn())

        # When
        applet._show_city_dialog()
        created_entry.set_text("be")
        created_entry.callbacks["changed"](created_entry)
        store = created_completion.model
        created_completion.callbacks["match-selected"](created_completion, store, 0)

        # Then
        assert len(store.rows) == 1
        add_city.assert_called_once_with(
            display="Berlin, Germany", lat=52.52, lng=13.41
        )
        created_dialog.destroy.assert_called_once()

    def test_build_tooltip_widget_minimal_branch(self):
        class _FakeBox:
            def __init__(self, orientation=None, spacing=0):
                self._children: list[object] = []

            def pack_start(self, child, expand, fill, padding):
                _ = expand, fill, padding
                self._children.append(child)

            def get_children(self):
                return self._children

        class _FakeLabel:
            def __init__(self, label=""):
                self._label = label

            def override_color(self, state, rgba):
                _ = state, rgba

            def set_markup(self, markup):
                self._label = markup

            def set_xalign(self, value):
                _ = value

        weather_applet_mod.Gtk = SimpleNamespace(  # type: ignore[assignment]
            Box=_FakeBox,
            Label=_FakeLabel,
            Orientation=SimpleNamespace(VERTICAL=1, HORIZONTAL=2),
            StateFlags=SimpleNamespace(NORMAL=1),
            IconSize=SimpleNamespace(LARGE_TOOLBAR=1),
            Image=SimpleNamespace(new_from_icon_name=lambda *args, **kwargs: object()),
        )

        # Given - no city
        applet = _make_applet()

        # When
        box = applet._build_tooltip_widget()

        # Then
        assert box is not None
        assert len(box.get_children()) == 1

    def test_build_tooltip_widget_with_weather_and_aqi(self):
        class _FakeBox:
            def __init__(self, orientation=None, spacing=0):
                self._children: list[object] = []

            def pack_start(self, child, expand, fill, padding):
                _ = expand, fill, padding
                self._children.append(child)

            def get_children(self):
                return self._children

        class _FakeLabel:
            def __init__(self, label=""):
                self._label = label

            def override_color(self, state, rgba):
                _ = state, rgba

            def set_markup(self, markup):
                self._label = markup

            def set_xalign(self, value):
                _ = value

        weather_applet_mod.Gtk = SimpleNamespace(  # type: ignore[assignment]
            Box=_FakeBox,
            Label=_FakeLabel,
            Orientation=SimpleNamespace(VERTICAL=1, HORIZONTAL=2),
            StateFlags=SimpleNamespace(NORMAL=1),
            IconSize=SimpleNamespace(LARGE_TOOLBAR=1),
            Image=SimpleNamespace(new_from_icon_name=lambda *args, **kwargs: object()),
        )

        # Given
        applet = _make_applet()
        applet._cities = [_BERLIN]
        applet._active_index = 0
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = _SAMPLE_AQI

        # When
        box = applet._build_tooltip_widget()

        # Then
        labels = [
            child._label
            for child in box.get_children()
            if isinstance(child, _FakeLabel)
        ]
        assert "Air: Fair" in labels
        assert "UV Index: 5.7" in labels
