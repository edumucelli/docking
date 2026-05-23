"""Tests for weather API -- WMO code mapping and data types."""

import pytest

import docking.applets.weather.api as weather_api_mod
from docking.applets.weather.api import (
    AirQualityData,
    DailyForecast,
    WeatherData,
    aqi_label,
    fetch_air_quality,
    fetch_weather,
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
        assert wmo_icon_name(code=code) == expected_icon

    @pytest.mark.parametrize(
        "code, expected_desc",
        [
            (0, "Clear sky"),
            (61, "Slight rain"),
            (95, "Thunderstorm"),
        ],
    )
    def test_wmo_description(self, code, expected_desc):
        assert wmo_description(code=code) == expected_desc

    def test_unknown_code_returns_fallback(self):
        assert wmo_icon_name(code=999) == "weather-few-clouds"
        assert wmo_description(code=999) == "Unknown"


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


class _Var:
    def __init__(self, value=None, values=None):
        self._value = value
        self._values = values or []

    def Value(self):
        return self._value

    def ValuesLength(self):
        return len(self._values)

    def Values(self, i):
        return self._values[i]


class _Current:
    def __init__(self, vars_):
        self._vars = vars_

    def Variables(self, i):
        return self._vars[i]


class _Daily:
    def __init__(self, vars_, base_time, interval):
        self._vars = vars_
        self._base_time = base_time
        self._interval = interval

    def Variables(self, i):
        return self._vars[i]

    def Time(self):
        return self._base_time

    def Interval(self):
        return self._interval


class _WeatherResp:
    def __init__(self):
        self._current = _Current([_Var(22.34), _Var(61)])
        self._daily = _Daily(
            [
                _Var(values=[0, 61]),
                _Var(values=[25.16, 21.44]),
                _Var(values=[17.78, 15.0]),
            ],
            base_time=1_710_000_000,
            interval=86_400,
        )

    def Current(self):
        return self._current

    def Daily(self):
        return self._daily


class _AqiResp:
    def __init__(self):
        self._current = _Current([_Var(37), _Var(15.44), _Var(9.93), _Var(6.32)])

    def Current(self):
        return self._current


class TestWeatherApiFetch:
    def test_fetch_weather_success(self, monkeypatch):
        class _Client:
            def weather_api(self, url, params):
                _ = (url, params)
                return [_WeatherResp()]

        monkeypatch.setattr(weather_api_mod, "_get_client", lambda: _Client())
        data = fetch_weather(lat=1.0, lng=2.0)
        assert isinstance(data, WeatherData)
        assert data.temperature == 22.3
        assert data.weather_code == 61
        assert len(data.daily) == 2
        assert data.daily[0].description == "Clear sky"

    def test_fetch_weather_error_returns_none(self, monkeypatch):
        class _Client:
            def weather_api(self, url, params):
                raise OSError("offline")

        monkeypatch.setattr(weather_api_mod, "_get_client", lambda: _Client())
        assert fetch_weather(lat=1.0, lng=2.0) is None

    def test_aqi_label_thresholds(self):
        assert aqi_label(10) == "Good"
        assert aqi_label(30) == "Fair"
        assert aqi_label(50) == "Moderate"
        assert aqi_label(70) == "Poor"
        assert aqi_label(90) == "Very Poor"
        assert aqi_label(120) == "Extremely Poor"

    def test_fetch_air_quality_success(self, monkeypatch):
        calls: list[dict[str, object]] = []

        class _Client:
            def weather_api(self, url, params):
                _ = url
                calls.append(params)
                return [_AqiResp()]

        monkeypatch.setattr(weather_api_mod, "_get_client", lambda: _Client())
        data = fetch_air_quality(lat=1.0, lng=2.0)
        assert isinstance(data, AirQualityData)
        assert data.aqi == 37
        assert data.pm10 == 15.4
        assert data.pm2_5 == 9.9
        assert data.uv_index == 6.3
        assert data.label == "Fair"
        assert calls == [
            {
                "latitude": 1.0,
                "longitude": 2.0,
                "current": ["european_aqi", "pm10", "pm2_5", "uv_index"],
            }
        ]

    def test_fetch_air_quality_error_returns_none(self, monkeypatch):
        class _Client:
            def weather_api(self, url, params):
                raise ValueError("bad payload")

        monkeypatch.setattr(weather_api_mod, "_get_client", lambda: _Client())
        assert fetch_air_quality(lat=1.0, lng=2.0) is None
