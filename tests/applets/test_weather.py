"""Tests for the weather applet."""

from __future__ import annotations

from unittest.mock import MagicMock

import docking.applets.weather as weather_mod
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
        applet.create_icon(48)
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
        applet.create_icon(48)
        assert "Berlin" in applet.item.name
        assert "22" in applet.item.name
        assert "Clear sky" in applet.item.name

    def test_tooltip_includes_daily_forecast(self):
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = _SAMPLE_WEATHER
        applet.create_icon(48)
        assert "Mon" in applet.item.name
        assert "Tue" in applet.item.name

    def test_tooltip_loading_state(self):
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = None
        applet.create_icon(48)
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
        applet.create_icon(48)
        assert "Air: Fair" in applet.item.name

    def test_tooltip_no_aqi_when_unavailable(self):
        applet = WeatherApplet(48)
        applet._city_display = "Berlin, Germany"
        applet._weather = _SAMPLE_WEATHER
        applet._air_quality = None
        applet.create_icon(48)
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
        monkeypatch.setattr(applet, "refresh_icon", refresh)
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
        monkeypatch.setattr(applet, "refresh_icon", refresh)
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
