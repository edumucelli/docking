"""Tests for the speedtest applet."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

from docking.applets.speedtest.api import SpeedtestError, run_librespeed
from docking.applets.speedtest.applet import SpeedtestApplet
from docking.applets.speedtest.librespeed import (
    LibrespeedError,
    Server,
    parse_server_list,
)
from docking.applets.speedtest.state import (
    SpeedtestPrefs,
    SpeedtestResult,
    build_tooltip,
    format_speed,
    prefs_from_mapping,
    prefs_payload,
    speed_tier,
)
from docking.core.config import Config

_TS = datetime(2026, 4, 24, 11, 0, tzinfo=timezone.utc)


def _result(
    *,
    download: float = 250.0,
    upload: float = 40.0,
    ping: float = 8.5,
    jitter: float = 1.2,
    server: str = "Test Node",
    ts: datetime = _TS,
) -> SpeedtestResult:
    return SpeedtestResult(
        download_mbps=download,
        upload_mbps=upload,
        ping_ms=ping,
        jitter_ms=jitter,
        server=server,
        timestamp=ts,
    )


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


def _make_applet(
    icon_size: int = 48, *, config: Config | None = None
) -> SpeedtestApplet:
    with patch("docking.applets.speedtest.applet.BackgroundWorker", _ImmediateWorker):
        return SpeedtestApplet(icon_size, config=config)


class TestFormatSpeed:
    def test_giga(self):
        assert format_speed(1500.0) == "1.5Gb"

    def test_triple_digit(self):
        assert format_speed(250.0) == "250Mb"

    def test_double_digit(self):
        assert format_speed(45.0) == "45Mb"

    def test_single_digit(self):
        assert format_speed(4.5) == "4.5Mb"


class TestSpeedTier:
    def test_fast_at_100(self):
        assert speed_tier(100.0) == "fast"

    def test_medium_at_25(self):
        assert speed_tier(25.0) == "medium"

    def test_slow_below_25(self):
        assert speed_tier(5.0) == "slow"

    def test_none_at_zero(self):
        assert speed_tier(0.0) == "none"


class TestParseServerList:
    def test_parses_typical_entry(self):
        raw = """[
            {"id": 1, "name": "Test Node", "server": "//example.test",
             "dlURL": "backend/garbage.php", "ulURL": "backend/empty.php",
             "pingURL": "backend/empty.php", "sponsorName": "Acme"}
        ]"""
        servers = parse_server_list(text=raw)
        assert len(servers) == 1
        s = servers[0]
        assert s.id == 1
        assert s.name == "Test Node"
        assert s.base_url == "https://example.test"
        assert s.endpoint(s.dl_url) == "https://example.test/backend/garbage.php"

    def test_base_url_with_scheme(self):
        raw = """[{"id": 2, "name": "x", "server": "https://x.test",
            "dlURL": "a", "ulURL": "b", "pingURL": "c"}]"""
        servers = parse_server_list(text=raw)
        assert servers[0].base_url == "https://x.test"

    def test_base_url_bare_host(self):
        raw = """[{"id": 3, "name": "y", "server": "y.test",
            "dlURL": "a", "ulURL": "b", "pingURL": "c"}]"""
        servers = parse_server_list(text=raw)
        assert servers[0].base_url == "https://y.test"

    def test_skips_malformed_entries(self):
        raw = """[
            {"id": 1, "name": "ok", "server": "//ok.test",
             "dlURL": "a", "ulURL": "b", "pingURL": "c"},
            "garbage",
            {}
        ]"""
        servers = parse_server_list(text=raw)
        assert len(servers) == 1
        assert servers[0].name == "ok"


class TestRunLibrespeedFailure:
    def test_wraps_librespeed_error(self):
        with patch(
            "docking.applets.speedtest.api.run_speedtest",
            side_effect=LibrespeedError("no servers"),
        ):
            got = run_librespeed()
        assert isinstance(got, SpeedtestError)
        assert "no servers" in got.message

    def test_wraps_network_error(self):
        with patch(
            "docking.applets.speedtest.api.run_speedtest",
            side_effect=OSError("host unreachable"),
        ):
            got = run_librespeed()
        assert isinstance(got, SpeedtestError)
        assert "host unreachable" in got.message

    def test_success_wraps_to_state_result(self):
        from docking.applets.speedtest.librespeed import (
            SpeedtestResult as RawResult,
        )

        raw = RawResult(
            download_mbps=123.4,
            upload_mbps=45.6,
            ping_ms=7.0,
            jitter_ms=0.5,
            server_name="Test",
            server_id=1,
        )
        with patch(
            "docking.applets.speedtest.api.run_speedtest",
            return_value=raw,
        ):
            got = run_librespeed()
        assert isinstance(got, SpeedtestResult)
        assert got.download_mbps == 123.4
        assert got.server == "Test"
        assert got.timestamp.tzinfo is not None


class TestServer:
    def test_endpoint_joins_with_trailing_slashes(self):
        s = Server(
            id=1,
            name="x",
            server="https://x.test/",
            dl_url="/backend/garbage.php",
            ul_url="",
            ping_url="",
        )
        assert s.endpoint(s.dl_url) == "https://x.test/backend/garbage.php"


class TestPrefsRoundTrip:
    def test_none_is_empty(self):
        assert prefs_from_mapping(None) == SpeedtestPrefs()

    def test_round_trips(self):
        r = _result()
        payload = prefs_payload(result=r)
        back = prefs_from_mapping(payload)
        assert back.last_result is not None
        assert back.last_result.download_mbps == r.download_mbps
        assert back.last_result.server == r.server

    def test_missing_timestamp_returns_empty(self):
        raw = {
            "last_result": {
                "download_mbps": 1.0,
                "upload_mbps": 1.0,
                "ping_ms": 1.0,
                "jitter_ms": 1.0,
                "server": "x",
            }
        }
        assert prefs_from_mapping(raw) == SpeedtestPrefs()

    def test_invalid_types_return_empty(self):
        raw = {"last_result": "garbage"}
        assert prefs_from_mapping(raw) == SpeedtestPrefs()


class TestBuildTooltip:
    def test_idle_without_result(self):
        text = build_tooltip(result=None, running=False, error=None)
        assert "Click" in text

    def test_running_state(self):
        text = build_tooltip(result=_result(), running=True, error=None)
        assert "Running" in text

    def test_error_state(self):
        text = build_tooltip(result=None, running=False, error="timeout")
        assert "timeout" in text

    def test_includes_all_metrics(self):
        text = build_tooltip(result=_result(), running=False, error=None)
        assert "Down" in text
        assert "Up" in text
        assert "Ping" in text
        assert "Jitter" in text
        assert "Test Node" in text


class TestAppletCreation:
    def test_creates_with_default_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_tooltip_when_no_result(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "Click" in applet.item.name

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = _make_applet(size)
            assert applet.create_icon(size) is not None


class TestAppletTooltip:
    def test_with_result(self):
        applet = _make_applet()
        applet._result = _result()
        applet.refresh_tooltip()
        assert "250.0" in applet.item.name
        assert "Test Node" in applet.item.name

    def test_while_running(self):
        applet = _make_applet()
        applet._running = True
        applet.refresh_tooltip()
        assert "Running" in applet.item.name


class TestAppletMenu:
    def test_empty_menu_has_run(self):
        applet = _make_applet()
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert any("Run Test" in label or "Running" in label for label in labels)

    def test_menu_with_result_shows_summary_and_copy(self):
        applet = _make_applet()
        applet._result = _result()
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert any("Down" in label for label in labels)
        assert any("Copy Last Result" in label for label in labels)


class TestAppletRun:
    def test_click_triggers_run_and_saves(self):
        config = Config(applet_prefs={})
        applet = _make_applet(config=config)
        mock_result = _result()
        with patch(
            "docking.applets.speedtest.applet.run_librespeed",
            return_value=mock_result,
        ):
            applet.on_clicked()
        assert applet._result == mock_result
        assert applet._running is False
        assert config.applet_prefs["speedtest"]["last_result"]["download_mbps"] == 250.0

    def test_error_result_keeps_previous(self):
        applet = _make_applet()
        previous = _result(download=100.0)
        applet._result = previous
        with patch(
            "docking.applets.speedtest.applet.run_librespeed",
            return_value=SpeedtestError(message="timeout"),
        ):
            applet.on_clicked()
        assert applet._result == previous
        assert applet._error == "timeout"
        assert applet._running is False

    def test_second_click_while_running_is_noop(self):
        applet = _make_applet()
        applet._running = True
        with patch("docking.applets.speedtest.applet.run_librespeed") as mocked:
            applet.on_clicked()
        mocked.assert_not_called()


class TestAppletPrefs:
    def test_loads_last_result_from_config(self):
        config = Config(
            applet_prefs={
                "speedtest": prefs_payload(result=_result())["last_result"]
                and prefs_payload(result=_result()),
            }
        )
        applet = _make_applet(config=config)
        assert applet._result is not None
        assert applet._result.download_mbps == 250.0
