"""Tests for weather API -- WMO code mapping and data types."""

import pytest

from docking.applets.weather.api import (
    DailyForecast,
    WeatherData,
    wmo_description,
    wmo_icon_name,
)


class TestWmoMapping:
    @pytest.mark.parametrize(
        "code, expected_icon",
        [
            (0, "weather-clear"),
            (1, "weather-few-clouds"),
            (3, "weather-overcast"),
            (45, "weather-fog"),
            (61, "weather-showers"),
            (71, "weather-snow"),
            (95, "weather-storm"),
        ],
    )
    def test_wmo_icon_name(self, code, expected_icon):
        assert wmo_icon_name(code) == expected_icon

    @pytest.mark.parametrize(
        "code, expected_desc",
        [
            (0, "Clear sky"),
            (61, "Slight rain"),
            (95, "Thunderstorm"),
        ],
    )
    def test_wmo_description(self, code, expected_desc):
        assert wmo_description(code) == expected_desc

    def test_unknown_code_returns_fallback(self):
        assert wmo_icon_name(999) == "weather-few-clouds"
        assert wmo_description(999) == "Unknown"


class TestWeatherData:
    def test_construction(self):
        data = WeatherData(
            temperature=22.5,
            weather_code=0,
            description="Clear sky",
            icon_name="weather-clear",
            daily=[
                DailyForecast("Mon", 0, "Clear sky", 25.0, 18.0),
                DailyForecast("Tue", 61, "Slight rain", 20.0, 15.0),
            ],
        )
        assert data.temperature == 22.5
        assert len(data.daily) == 2
        assert data.daily[0].date == "Mon"
        assert data.daily[1].temp_max == 20.0
