"""Weather JSON parsing, persistent caching, and HTTP retry behavior."""

import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.message import Message
from email.utils import format_datetime
from http.client import IncompleteRead, RemoteDisconnected
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

import docking.applets.weather.api as weather_api_mod
from docking.applets.http import http_get_json
from docking.applets.weather.api import (
    API_TIMEOUT_S,
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


@pytest.fixture(autouse=True)
def isolate_weather(monkeypatch, tmp_path):
    monkeypatch.setattr(weather_api_mod, "_CACHE_DIR", tmp_path / "weather")
    monkeypatch.setattr(
        weather_api_mod,
        "time",
        SimpleNamespace(time=Mock(return_value=1_710_000_000.0), sleep=Mock()),
    )
    transport = Mock(side_effect=AssertionError("Unexpected network request"))
    monkeypatch.setattr(weather_api_mod, "http_get_json", transport)
    return transport


@pytest.fixture
def weather_response():
    return {
        "current": {"temperature_2m": 22.34, "weather_code": 61},
        "daily": {
            "time": [1_710_028_800 + day * 86_400 for day in range(5)],
            "weather_code": [0, 61, 3, 71, 95],
            "temperature_2m_max": [25.16, 21.44, 20.0, 10.0, 18.0],
            "temperature_2m_min": [17.78, 15.0, 12.0, 2.0, 10.0],
        },
    }


@pytest.fixture
def air_quality_response():
    return {
        "current": {
            "european_aqi": 37,
            "pm10": 15.44,
            "pm2_5": 9.93,
            "uv_index": 6.32,
        }
    }


@pytest.fixture
def weather_server(weather_response, air_quality_response):
    requests = []
    responses = [
        (500, {"error": True}),
        (200, weather_response),
        (200, air_quality_response),
    ]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, self.headers["User-Agent"]))
            status, response = responses.pop(0)
            payload = json.dumps(response).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, _format, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            thread.join(timeout=5)


def test_fetch_weather_success(isolate_weather, weather_response):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    data = fetch_weather(lat=1.0, lng=2.0)
    assert isinstance(data, WeatherData)
    assert data.temperature == 22.3
    assert data.weather_code == 61
    assert data.icon_name == "weather-showers"
    assert [day.date for day in data.daily] == ["Sun", "Mon", "Tue", "Wed", "Thu"]
    assert data.daily[0] == DailyForecast("Sun", 0, "Clear sky", 25.2, 17.8)
    assert data.daily[1].description == "Slight rain"
    url = isolate_weather.call_args.args[0]
    assert urlsplit(url).path == "/v1/forecast"
    assert parse_qs(urlsplit(url).query) == {
        "latitude": ["1.0"],
        "longitude": ["2.0"],
        "current": ["temperature_2m,weather_code"],
        "daily": ["weather_code,temperature_2m_max,temperature_2m_min"],
        "forecast_days": ["5"],
        "timezone": ["GMT"],
        "timeformat": ["unixtime"],
    }
    assert isolate_weather.call_args.kwargs == {"timeout": API_TIMEOUT_S}


def test_fetch_air_quality_success(isolate_weather, air_quality_response):
    isolate_weather.side_effect = None
    isolate_weather.return_value = air_quality_response
    assert fetch_air_quality(1.0, 2.0) == AirQualityData(37, 9.9, 15.4, "Fair", 6.3)
    url = isolate_weather.call_args.args[0]
    assert urlsplit(url).netloc == "air-quality-api.open-meteo.com"
    assert parse_qs(urlsplit(url).query)["current"] == [
        "european_aqi,pm10,pm2_5,uv_index"
    ]
    assert isolate_weather.call_args.kwargs == {"timeout": API_TIMEOUT_S}


@pytest.mark.parametrize(
    "aqi,label",
    [
        (20, "Good"),
        (40, "Fair"),
        (60, "Moderate"),
        (80, "Poor"),
        (100, "Very Poor"),
        (101, "Extremely Poor"),
    ],
)
def test_aqi_label_thresholds(aqi, label):
    assert aqi_label(aqi) == label


@pytest.mark.parametrize("value", [None, "bad", True, float("nan"), float("inf")])
def test_invalid_optional_uv_is_ignored(isolate_weather, air_quality_response, value):
    air_quality_response["current"]["uv_index"] = value
    isolate_weather.side_effect = None
    isolate_weather.return_value = air_quality_response
    assert fetch_air_quality(1.0, 2.0).uv_index is None


def test_missing_uv_is_optional(isolate_weather, air_quality_response):
    del air_quality_response["current"]["uv_index"]
    isolate_weather.side_effect = None
    isolate_weather.return_value = air_quality_response
    assert fetch_air_quality(1.0, 2.0).uv_index is None


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_optional_uv_is_cached_as_null(
    isolate_weather, air_quality_response, value
):
    air_quality_response["current"]["uv_index"] = value
    isolate_weather.side_effect = None
    isolate_weather.return_value = air_quality_response
    first = fetch_air_quality(1.0, 2.0)
    assert first.uv_index is None
    assert fetch_air_quality(1.0, 2.0) == first
    assert isolate_weather.call_count == 1
    path = next(weather_api_mod._CACHE_DIR.rglob("*.json"))
    cached = json.loads(
        path.read_text(), parse_constant=lambda _: pytest.fail("Nonstandard JSON")
    )
    assert cached["payload"]["current"] == {
        **air_quality_response["current"],
        "uv_index": None,
    }
    assert air_quality_response["current"]["uv_index"] is value


@pytest.mark.parametrize("length", [0, 2])
def test_coherent_shorter_forecast(isolate_weather, weather_response, length):
    weather_response["daily"] = {
        name: values[:length] for name, values in weather_response["daily"].items()
    }
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert len(fetch_weather(1.0, 2.0).daily) == length


def test_unknown_code_keeps_fallback(isolate_weather, weather_response):
    weather_response["current"]["weather_code"] = 999
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    data = fetch_weather(1.0, 2.0)
    assert data.description == "Unknown"
    assert data.icon_name == "weather-few-clouds"


@pytest.mark.parametrize("daily", [False, True])
@pytest.mark.parametrize("value", [61.9, -0.1])
def test_fractional_weather_code_is_rejected(
    isolate_weather, weather_response, daily, value
):
    if daily:
        weather_response["daily"]["weather_code"][0] = value
    else:
        weather_response["current"]["weather_code"] = value
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is None
    assert not list(weather_api_mod._CACHE_DIR.rglob("*.json"))


@pytest.mark.parametrize("daily", [False, True])
def test_integral_float_weather_codes_remain_valid(
    isolate_weather, weather_response, daily
):
    if daily:
        weather_response["daily"]["weather_code"][0] = 61.0
    else:
        weather_response["current"]["weather_code"] = 61.0
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    data = fetch_weather(1.0, 2.0)
    assert data is not None
    assert (data.daily[0].code if daily else data.weather_code) == 61


@pytest.mark.parametrize("value", [None, "22.3", True, float("nan"), float("inf")])
def test_invalid_required_readings_are_not_cached(
    isolate_weather, weather_response, value
):
    weather_response["current"]["temperature_2m"] = value
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is None
    assert not list(weather_api_mod._CACHE_DIR.rglob("*.json"))


@pytest.mark.parametrize("fetch", [fetch_weather, fetch_air_quality])
@pytest.mark.parametrize(
    "payload", [None, [], {}, {"error": True, "reason": "Bad request"}]
)
def test_invalid_response_returns_none(isolate_weather, fetch, payload):
    isolate_weather.side_effect = None
    isolate_weather.return_value = payload
    assert fetch(1.0, 2.0) is None
    assert isolate_weather.call_count == 1


@pytest.mark.parametrize(
    "field", ["time", "weather_code", "temperature_2m_max", "temperature_2m_min"]
)
def test_mismatched_forecast_arrays_are_rejected(
    isolate_weather, weather_response, field
):
    weather_response["daily"][field].pop()
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is None


def test_cache_persists_and_expires(isolate_weather, weather_response):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    first = fetch_weather(1.0, 2.0)
    assert fetch_weather(1.0, 2.0) == first
    assert isolate_weather.call_count == 1
    assert len(list(weather_api_mod._CACHE_DIR.rglob("*.json"))) == 1
    weather_api_mod.time.time.return_value += weather_api_mod.REFRESH_INTERVAL - 1
    assert fetch_weather(1.0, 2.0) == first
    assert isolate_weather.call_count == 1
    weather_api_mod.time.time.return_value += 1
    assert fetch_weather(1.0, 2.0) == first
    assert isolate_weather.call_count == 2


def test_expired_cache_does_not_hide_network_failure(isolate_weather, weather_response):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is not None
    weather_api_mod.time.time.return_value += weather_api_mod.REFRESH_INTERVAL
    isolate_weather.side_effect = URLError("offline")
    assert fetch_weather(1.0, 2.0) is None


def test_cache_keys_include_endpoint_and_coordinates(
    isolate_weather, weather_response, air_quality_response
):
    isolate_weather.side_effect = [
        weather_response,
        weather_response,
        air_quality_response,
    ]
    assert fetch_weather(1.0, 2.0) is not None
    assert fetch_weather(1.0, 3.0) is not None
    assert fetch_air_quality(1.0, 2.0) is not None
    assert fetch_weather(1.0, 2.0) is not None
    assert fetch_air_quality(1.0, 2.0) is not None
    assert isolate_weather.call_count == 3


def test_writing_cache_prunes_expired_entries_but_preserves_fresh_and_other_files(
    isolate_weather, weather_response
):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is not None
    expired = next(weather_api_mod._CACHE_DIR.rglob("*.json"))
    legacy = weather_api_mod._CACHE_DIR / "responses.sqlite"
    legacy.write_bytes(b"old cache")
    temporary = expired.parent / "temporary"
    temporary.write_bytes(b"incomplete write")
    weather_api_mod.time.time.return_value += 1
    assert fetch_weather(1.0, 3.0) is not None
    weather_api_mod.time.time.return_value += weather_api_mod.REFRESH_INTERVAL - 1
    assert fetch_weather(1.0, 4.0) is not None
    assert not expired.exists()
    assert len(list(expired.parent.glob("*.json"))) == 2
    assert legacy.read_bytes() == b"old cache"
    assert temporary.read_bytes() == b"incomplete write"
    assert fetch_weather(1.0, 3.0) is not None
    assert isolate_weather.call_count == 3


def test_cache_pruning_failure_does_not_lose_fresh_data(
    monkeypatch, isolate_weather, weather_response
):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is not None
    expired = next(weather_api_mod._CACHE_DIR.rglob("*.json"))
    weather_api_mod.time.time.return_value += weather_api_mod.REFRESH_INTERVAL
    unlink = Path.unlink

    def deny_expired(path, *args, **kwargs):
        if path == expired:
            raise PermissionError("read only")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny_expired)
    assert fetch_weather(1.0, 3.0).temperature == 22.3
    assert expired.exists()
    assert len(list(expired.parent.glob("*.json"))) == 2


@pytest.mark.parametrize(
    "content", [b"{", b"\xff", b"[]", b"{}", b'{"fetched_at":true}']
)
def test_corrupt_cache_is_refreshed(isolate_weather, weather_response, content):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is not None
    path = next(weather_api_mod._CACHE_DIR.rglob("*.json"))
    path.write_bytes(content)
    assert fetch_weather(1.0, 2.0) is not None
    assert isolate_weather.call_count == 2
    assert json.loads(path.read_text())["payload"] == weather_response


def test_invalid_cached_payload_is_refreshed(isolate_weather, weather_response):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is not None
    path = next(weather_api_mod._CACHE_DIR.rglob("*.json"))
    cached = json.loads(path.read_text())
    cached["payload"] = {"current": {}}
    path.write_text(json.dumps(cached))
    assert fetch_weather(1.0, 2.0) is not None
    assert isolate_weather.call_count == 2


def test_future_cache_timestamp_is_not_fresh(isolate_weather, weather_response):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is not None
    weather_api_mod.time.time.return_value -= 1
    assert fetch_weather(1.0, 2.0) is not None
    assert isolate_weather.call_count == 2


def test_unavailable_cache_does_not_lose_fresh_data(isolate_weather, weather_response):
    weather_api_mod._CACHE_DIR.write_text("not a directory")
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0).temperature == 22.3
    assert weather_api_mod._CACHE_DIR.read_text() == "not a directory"


def test_failed_cache_replace_cleans_temporary_file(
    monkeypatch, isolate_weather, weather_response
):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    monkeypatch.setattr(Path, "replace", Mock(side_effect=PermissionError("read only")))
    assert fetch_weather(1.0, 2.0).temperature == 22.3
    assert list((weather_api_mod._CACHE_DIR / "json-v1").iterdir()) == []


def test_concurrent_cache_writers_leave_complete_json(
    isolate_weather, weather_response
):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: fetch_weather(1.0, 2.0), range(24)))
    assert all(result.temperature == 22.3 for result in results)
    files = list((weather_api_mod._CACHE_DIR / "json-v1").iterdir())
    assert len(files) == 1
    assert json.loads(files[0].read_text())["payload"] == weather_response


def _http_error(code, retry_after=None):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    body = io.BytesIO(b"server error")
    return HTTPError("https://example.test/", code, "failed", headers, body), body


@pytest.mark.parametrize("code", [400, 401, 403, 404, 413, 429, 503])
def test_nonretryable_http_error_is_closed(isolate_weather, code):
    error, body = _http_error(code)
    isolate_weather.side_effect = error
    assert fetch_weather(1.0, 2.0) is None
    assert isolate_weather.call_count == 1
    weather_api_mod.time.sleep.assert_not_called()
    assert body.closed


@pytest.mark.parametrize("code", [500, 502, 504])
def test_retryable_http_error_recovers(isolate_weather, weather_response, code):
    error, body = _http_error(code)
    isolate_weather.side_effect = [error, weather_response]
    assert fetch_weather(1.0, 2.0).temperature == 22.3
    assert isolate_weather.call_count == 2
    weather_api_mod.time.sleep.assert_not_called()
    assert body.closed


@pytest.mark.parametrize("code", [413, 429, 503, 500])
@pytest.mark.parametrize("header,delay", [("3", 3.0), ("60", 60.0), ("date", 3.0)])
def test_retry_after_is_honored(isolate_weather, weather_response, code, header, delay):
    if header == "date":
        date = datetime.fromtimestamp(weather_api_mod.time.time() + 3, timezone.utc)
        header = format_datetime(date, usegmt=True)
    error, body = _http_error(code, header)
    isolate_weather.side_effect = [error, weather_response]
    assert fetch_weather(1.0, 2.0) is not None
    weather_api_mod.time.sleep.assert_called_once_with(delay)
    assert body.closed


@pytest.mark.parametrize("code", [429, 503, 500])
@pytest.mark.parametrize("header", ["61", "3600", "date"])
def test_excessive_retry_after_fails_without_sleep(isolate_weather, code, header):
    if header == "date":
        date = datetime.fromtimestamp(weather_api_mod.time.time() + 61, timezone.utc)
        header = format_datetime(date, usegmt=True)
    error, body = _http_error(code, header)
    isolate_weather.side_effect = error
    assert fetch_weather(1.0, 2.0) is None
    isolate_weather.assert_called_once()
    weather_api_mod.time.sleep.assert_not_called()
    assert body.closed


@pytest.mark.parametrize("header", ["bad date", "-3", "0", "9" * 400])
def test_invalid_retry_after_uses_backoff(isolate_weather, weather_response, header):
    first, _ = _http_error(500)
    second, _ = _http_error(500, header)
    isolate_weather.side_effect = [first, second, weather_response]
    assert fetch_weather(1.0, 2.0) is not None
    weather_api_mod.time.sleep.assert_called_once_with(0.4)


@pytest.mark.parametrize(
    "error",
    [
        OSError("offline"),
        TimeoutError(),
        URLError("offline"),
        RemoteDisconnected(),
        IncompleteRead(b"partial"),
    ],
)
def test_network_failure_exhausts_five_retries(isolate_weather, error):
    isolate_weather.side_effect = error
    assert fetch_weather(1.0, 2.0) is None
    assert isolate_weather.call_count == 6
    assert [call.args[0] for call in weather_api_mod.time.sleep.call_args_list] == [
        0.4,
        0.8,
        1.6,
        3.2,
    ]
    assert all(
        call.kwargs == {"timeout": API_TIMEOUT_S}
        for call in isolate_weather.call_args_list
    )


def test_http_retry_exhaustion_closes_every_response(isolate_weather):
    errors = [_http_error(500) for _ in range(6)]
    isolate_weather.side_effect = [error for error, _ in errors]
    assert fetch_air_quality(1.0, 2.0) is None
    assert isolate_weather.call_count == 6
    assert all(body.closed for _, body in errors)


def test_malformed_json_is_not_retried(isolate_weather):
    isolate_weather.side_effect = json.JSONDecodeError("invalid", "{", 1)
    assert fetch_weather(1.0, 2.0) is None
    assert isolate_weather.call_count == 1


@pytest.mark.parametrize(
    "fetch,action",
    [(fetch_weather, "fetch_weather"), (fetch_air_quality, "fetch_air_quality")],
)
@pytest.mark.parametrize(
    "error,reason",
    [
        (TimeoutError("private request details"), "TimeoutError"),
        (URLError(TimeoutError("private request details")), "TimeoutError"),
        (ValueError("private response details"), "ValueError"),
    ],
)
def test_fetch_logs_safe_failure_diagnostics(
    isolate_weather, caplog, fetch, action, error, reason
):
    isolate_weather.side_effect = error
    assert fetch(1.0, 2.0) is None
    record = caplog.records[-1]
    assert reason in record.getMessage()
    assert record.action == action
    assert record.applet_id == "weather"
    assert record.exc_info is None
    assert "private" not in caplog.text


@pytest.mark.parametrize("fetch", [fetch_weather, fetch_air_quality])
def test_fetch_logs_http_status_without_request_url(isolate_weather, caplog, fetch):
    error, _ = _http_error(404)
    isolate_weather.side_effect = error
    assert fetch(1.0, 2.0) is None
    assert "HTTP 404" in caplog.text
    assert "https://" not in caplog.text
    assert "latitude" not in caplog.text


def test_real_http_json_retry_and_cache(monkeypatch, weather_server):
    base, requests = weather_server
    monkeypatch.setattr(weather_api_mod, "_API_URL", base + "/forecast")
    monkeypatch.setattr(weather_api_mod, "_AQI_URL", base + "/air-quality")
    monkeypatch.setattr(weather_api_mod, "http_get_json", http_get_json)
    weather = fetch_weather(1.0, 2.0)
    assert weather is not None and weather.temperature == 22.3
    assert fetch_air_quality(1.0, 2.0).aqi == 37
    assert fetch_weather(1.0, 2.0) == weather
    assert len(requests) == 3
    assert urlsplit(requests[0][0]).path == "/forecast"
    assert urlsplit(requests[2][0]).path == "/air-quality"
    assert all(user_agent.startswith("DockingApplet/") for _, user_agent in requests)


def test_weather_runs_without_old_dependencies_and_reuses_cache_in_new_process(
    isolate_weather, weather_response, weather_server
):
    isolate_weather.side_effect = None
    isolate_weather.return_value = weather_response
    assert fetch_weather(1.0, 2.0) is not None
    root = Path(__file__).resolve().parents[2]
    script = """
import importlib.abc
import sys
from pathlib import Path

blocked = {
    'openmeteo_requests', 'openmeteo_sdk', 'flatbuffers', 'niquests',
    'urllib3_future', 'h11', 'jh2', 'qh3', 'wassima', 'requests', 'urllib3',
    'charset_normalizer', 'idna', 'certifi', 'attrs', 'attr', 'cattrs',
    'platformdirs', 'url_normalize', 'typing_extensions', 'requests_cache',
    'retry_requests',
}
class BlockDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, BlockDependencies())
sys.path.insert(0, sys.argv[1])
from docking.applets.weather import api
api._CACHE_DIR = Path(sys.argv[2])
api.time.time = lambda: 1710000000.0
data = api.fetch_weather(1.0, 2.0)
assert data is not None and data.temperature == 22.3
api._CACHE_DIR = api._CACHE_DIR / 'cold'
api._API_URL = sys.argv[3] + '/forecast'
api._AQI_URL = sys.argv[3] + '/air-quality'
data = api.fetch_weather(1.0, 2.0)
assert data is not None and data.temperature == 22.3
quality = api.fetch_air_quality(1.0, 2.0)
assert quality is not None and quality.aqi == 37
assert not blocked.intersection(sys.modules)
print('weather cache and imports passed')
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            script,
            str(root),
            str(weather_api_mod._CACHE_DIR),
            weather_server[0],
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert result.stdout.strip() == "weather cache and imports passed"
    assert len(weather_server[1]) == 3
