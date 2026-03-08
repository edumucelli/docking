"""Tests for the weather applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.weather as weather_mod
import docking.applets.weather.applet as weather_applet_mod
from docking.applets.weather import WeatherApplet
from docking.applets.weather.api import (
    AirQualityData,
    DailyForecast,
    WeatherData,
    aqi_label,
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


class TestWeatherAppletCreation:
    def test_creates_with_default_icon(self):
        applet = WeatherApplet(48)
        assert applet.item.icon is not None

    def test_default_tooltip_no_city(self):
        applet = WeatherApplet(48)
        applet.refresh_tooltip()
        assert "no city" in applet.item.name.lower()

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = WeatherApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None


class TestWeatherTooltip:
    def test_tooltip_shows_city_and_temp(self):
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = _SAMPLE_WEATHER
        applet.refresh_tooltip()
        assert "Berlin" in applet.item.name
        assert "22" in applet.item.name
        assert "Clear sky" in applet.item.name

    def test_tooltip_includes_daily_forecast(self):
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = _SAMPLE_WEATHER
        applet.refresh_tooltip()
        assert "Mon" in applet.item.name
        assert "Tue" in applet.item.name

    def test_tooltip_loading_state(self):
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = None
        applet.refresh_tooltip()
        assert "loading" in applet.item.name.lower()


class TestWeatherTemperatureOverlay:
    def test_overlay_renders_with_weather_data(self):
        applet = WeatherApplet(48)
        applet._weather = _SAMPLE_WEATHER
        applet._show_temperature = True
        pixbuf = applet.create_icon(48)
        assert pixbuf is not None

    def test_no_overlay_when_disabled(self):
        # Given temperature overlay disabled
        applet = WeatherApplet(48)
        applet._weather = _SAMPLE_WEATHER
        applet._show_temperature = False
        # When
        pixbuf = applet.create_icon(48)
        assert pixbuf is not None

    def test_no_overlay_without_weather_data(self):
        applet = WeatherApplet(48)
        applet._weather = None
        applet._show_temperature = True
        pixbuf = applet.create_icon(48)
        assert pixbuf is not None


class TestWeatherMenu:
    def test_menu_has_show_temp_and_change_city(self):
        applet = WeatherApplet(48)
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert "Show Temperature" in labels
        assert "Change City..." in labels

    def test_menu_includes_city_header_when_set(self):
        applet = WeatherApplet(48)
        applet._city_display = "Tokyo, Japan"
        applet._weather = _SAMPLE_WEATHER
        items = applet.get_menu_items()
        # First item should be the city header (insensitive)
        assert "Tokyo" in items[0].get_label()
        assert not items[0].get_sensitive()

    def test_menu_no_city_header_when_unset(self):
        applet = WeatherApplet(48)
        items = applet.get_menu_items()
        # No city header, just show_temp + change_city
        assert len(items) == 2


_SAMPLE_AQI = AirQualityData(aqi=28, pm2_5=8.1, pm10=9.1, label="Fair")


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
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = _SAMPLE_AQI
        applet.refresh_tooltip()
        assert "Air: Fair" in applet.item.name

    def test_tooltip_no_aqi_when_unavailable(self):
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = None
        applet.refresh_tooltip()
        assert "Air:" not in applet.item.name


class TestWeatherPrefs:
    def test_loads_city_from_config(self):
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
        applet = WeatherApplet(48, config=config)
        assert applet._city_display == "Paris, France"
        assert applet._lat == 48.85
        assert applet._show_temperature is False

    def test_saves_prefs_on_city_select(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = WeatherApplet(48, config=config)

        applet._select_city("London, United Kingdom", 51.51, -0.13)

        reloaded = Config.load(path)
        prefs = reloaded.applet_prefs["weather"]
        assert prefs["city_display"] == "London, United Kingdom"
        assert prefs["lat"] == 51.51

    def test_saves_show_temperature_pref(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = WeatherApplet(48, config=config)

        applet._show_temperature = False
        applet._save_prefs()

        reloaded = Config.load(path)
        assert reloaded.applet_prefs["weather"]["show_temperature"] is False


class _ImmediateThread:
    def __init__(self, target, daemon=True):
        self._target = target
        self.daemon = daemon

    def start(self):
        self._target()


class TestWeatherAsyncFetch:
    def test_on_fetch_result_ignores_stale_request(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._fetch_request_id = 2
        applet._weather = None
        applet._air_quality = None
        refresh = MagicMock()
        monkeypatch.setattr(applet, "refresh_presentation", refresh)
        # When
        result = applet._on_fetch_result(1, _SAMPLE_WEATHER, _SAMPLE_AQI)
        # Then
        assert result is False
        assert applet._weather is None
        assert applet._air_quality is None
        refresh.assert_not_called()

    def test_on_fetch_result_applies_latest_request(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._fetch_request_id = 3
        refresh = MagicMock()
        monkeypatch.setattr(applet, "refresh_presentation", refresh)
        # When
        result = applet._on_fetch_result(3, _SAMPLE_WEATHER, _SAMPLE_AQI)
        # Then
        assert result is False
        assert applet._weather == _SAMPLE_WEATHER
        assert applet._air_quality == _SAMPLE_AQI
        refresh.assert_called_once()

    def test_fetch_async_uses_coordinate_snapshot(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._lat = 10.0
        applet._lng = 20.0

        def fake_fetch_weather(lat, lng):
            applet._lat = 99.0
            applet._lng = 88.0
            assert lat == 10.0
            assert lng == 20.0
            return _SAMPLE_WEATHER

        fetch_aqi = MagicMock(return_value=_SAMPLE_AQI)
        monkeypatch.setattr(weather_mod, "fetch_weather", fake_fetch_weather)
        monkeypatch.setattr(weather_mod, "fetch_air_quality", fetch_aqi)
        monkeypatch.setattr(weather_mod.threading, "Thread", _ImmediateThread)
        monkeypatch.setattr(weather_mod.GLib, "idle_add", lambda cb: cb())
        # When
        applet._fetch_async()
        # Then
        fetch_aqi.assert_called_once_with(lat=10.0, lng=20.0)
        assert applet._weather == _SAMPLE_WEATHER
        assert applet._air_quality == _SAMPLE_AQI


class TestWeatherLifecycleAndInteractions:
    def test_start_schedules_timer_and_fetches_when_city_selected(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        monkeypatch.setattr(weather_mod.GLib, "timeout_add_seconds", lambda *_a: 99)

        # When
        applet.start(notify=lambda: None)

        # Then
        assert applet._timer_id == 99
        fetch.assert_called_once()

    def test_stop_removes_active_timer(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._timer_id = 77
        remove = MagicMock()
        monkeypatch.setattr(weather_mod.GLib, "source_remove", remove)

        # When
        applet.stop()

        # Then
        remove.assert_called_once_with(77)
        assert applet._timer_id == 0

    def test_tick_fetches_when_city_is_set(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._city_display = "Tokyo, Japan"
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)

        # When
        result = applet._tick()

        # Then
        assert result is True
        fetch.assert_called_once()

    def test_on_toggle_temperature_saves_and_refreshes(self):
        # Given
        applet = WeatherApplet(48)
        applet._save_prefs = MagicMock()
        applet.refresh_presentation = MagicMock()
        widget = MagicMock()
        widget.get_active.return_value = False

        # When
        applet._on_toggle_temperature(widget)

        # Then
        assert applet._show_temperature is False
        applet._save_prefs.assert_called_once()
        applet.refresh_presentation.assert_called_once()

    def test_on_clicked_noops_without_city(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._city_display = ""
        launch = MagicMock()
        monkeypatch.setattr(
            weather_applet_mod.Gio.AppInfo,
            "launch_default_for_uri",
            launch,
        )

        # When
        applet.on_clicked()

        # Then
        launch.assert_not_called()

    def test_start_without_city_does_not_fetch(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._city_display = ""
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)
        monkeypatch.setattr(weather_mod.GLib, "timeout_add_seconds", lambda *_a: 51)

        # When
        applet.start(notify=lambda: None)

        # Then
        assert applet._timer_id == 51
        fetch.assert_not_called()

    def test_tick_without_city_does_not_fetch(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._city_display = ""
        fetch = MagicMock()
        monkeypatch.setattr(applet, "_fetch_async", fetch)

        # When
        result = applet._tick()

        # Then
        assert result is True
        fetch.assert_not_called()

    def test_on_clicked_logs_when_launch_fails(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        monkeypatch.setattr(weather_applet_mod.GLib, "Error", Exception)
        monkeypatch.setattr(
            weather_applet_mod.Gio.AppInfo,
            "launch_default_for_uri",
            MagicMock(side_effect=Exception("boom")),
        )

        # When
        applet.on_clicked()

        # Then
        weather_applet_mod.Gio.AppInfo.launch_default_for_uri.assert_called_once()


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

    def set_default_size(self, *_args) -> None:
        return

    def set_position(self, *_args) -> None:
        return

    def get_content_area(self):
        return self._content

    def show_all(self) -> None:
        return


class TestWeatherDialogAndWidget:
    def test_show_city_dialog_search_and_select(self, monkeypatch):
        # Given
        applet = WeatherApplet(48)
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
        select = MagicMock()
        monkeypatch.setattr(applet, "_select_city", select)

        # When
        applet._show_city_dialog()
        created_entry.set_text("be")
        created_entry.callbacks["changed"](created_entry)
        store = created_completion.model
        created_completion.callbacks["match-selected"](created_completion, store, 0)

        # Then
        assert len(store.rows) == 1
        select.assert_called_once_with(display="Berlin, Germany", lat=52.52, lng=13.41)
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

        # Given
        applet = WeatherApplet(48)
        applet._city_display = ""
        applet._weather = None

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
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = _SAMPLE_AQI

        # When
        box = applet._build_tooltip_widget()

        # Then
        # city + current + air + 2 daily rows
        assert len(box.get_children()) >= 5
