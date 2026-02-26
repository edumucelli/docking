"""Tests for the weather applet."""

from docking.applets.weather import WeatherApplet
from docking.applets.weather.api import DailyForecast, WeatherData
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
        # When -- should return base icon without Cairo compositing
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
