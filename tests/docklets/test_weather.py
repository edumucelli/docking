"""Tests for the weather docklet."""

from docking.core.config import Config
from docking.docklets.weather import WeatherDocklet
from docking.docklets.weather.api import DailyForecast, WeatherData

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


class TestWeatherDockletCreation:
    def test_creates_with_default_icon(self):
        docklet = WeatherDocklet(48)
        assert docklet.item.icon is not None

    def test_default_tooltip_no_city(self):
        docklet = WeatherDocklet(48)
        docklet.create_icon(48)
        assert "no city" in docklet.item.name.lower()

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            docklet = WeatherDocklet(size)
            pixbuf = docklet.create_icon(size)
            assert pixbuf is not None


class TestWeatherTooltip:
    def test_tooltip_shows_city_and_temp(self):
        docklet = WeatherDocklet(48)
        docklet._city_display = "Berlin, Germany"
        docklet._weather = _SAMPLE_WEATHER
        docklet.create_icon(48)
        assert "Berlin" in docklet.item.name
        assert "22" in docklet.item.name
        assert "Clear sky" in docklet.item.name

    def test_tooltip_includes_daily_forecast(self):
        docklet = WeatherDocklet(48)
        docklet._city_display = "Berlin, Germany"
        docklet._weather = _SAMPLE_WEATHER
        docklet.create_icon(48)
        assert "Mon" in docklet.item.name
        assert "Tue" in docklet.item.name

    def test_tooltip_loading_state(self):
        docklet = WeatherDocklet(48)
        docklet._city_display = "Berlin, Germany"
        docklet._weather = None
        docklet.create_icon(48)
        assert "loading" in docklet.item.name.lower()


class TestWeatherTemperatureOverlay:
    def test_overlay_renders_with_weather_data(self):
        docklet = WeatherDocklet(48)
        docklet._weather = _SAMPLE_WEATHER
        docklet._show_temperature = True
        pixbuf = docklet.create_icon(48)
        assert pixbuf is not None

    def test_no_overlay_when_disabled(self):
        # Given temperature overlay disabled
        docklet = WeatherDocklet(48)
        docklet._weather = _SAMPLE_WEATHER
        docklet._show_temperature = False
        # When -- should return base icon without Cairo compositing
        pixbuf = docklet.create_icon(48)
        assert pixbuf is not None

    def test_no_overlay_without_weather_data(self):
        docklet = WeatherDocklet(48)
        docklet._weather = None
        docklet._show_temperature = True
        pixbuf = docklet.create_icon(48)
        assert pixbuf is not None


class TestWeatherMenu:
    def test_menu_has_show_temp_and_change_city(self):
        docklet = WeatherDocklet(48)
        items = docklet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert "Show Temperature" in labels
        assert "Change City..." in labels

    def test_menu_includes_city_header_when_set(self):
        docklet = WeatherDocklet(48)
        docklet._city_display = "Tokyo, Japan"
        docklet._weather = _SAMPLE_WEATHER
        items = docklet.get_menu_items()
        # First item should be the city header (insensitive)
        assert "Tokyo" in items[0].get_label()
        assert not items[0].get_sensitive()

    def test_menu_no_city_header_when_unset(self):
        docklet = WeatherDocklet(48)
        items = docklet.get_menu_items()
        # No city header, just show_temp + change_city
        assert len(items) == 2


class TestWeatherPrefs:
    def test_loads_city_from_config(self):
        config = Config(
            docklet_prefs={
                "weather": {
                    "city_display": "Paris, France",
                    "lat": 48.85,
                    "lng": 2.35,
                    "show_temperature": False,
                }
            }
        )
        docklet = WeatherDocklet(48, config=config)
        assert docklet._city_display == "Paris, France"
        assert docklet._lat == 48.85
        assert docklet._show_temperature is False

    def test_saves_prefs_on_city_select(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        docklet = WeatherDocklet(48, config=config)

        docklet._select_city("London, United Kingdom", 51.51, -0.13)

        reloaded = Config.load(path)
        prefs = reloaded.docklet_prefs["weather"]
        assert prefs["city_display"] == "London, United Kingdom"
        assert prefs["lat"] == 51.51

    def test_saves_show_temperature_pref(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        docklet = WeatherDocklet(48, config=config)

        docklet._show_temperature = False
        docklet._save_prefs()

        reloaded = Config.load(path)
        assert reloaded.docklet_prefs["weather"]["show_temperature"] is False
