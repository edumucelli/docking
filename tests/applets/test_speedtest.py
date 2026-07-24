"""Tests for the speedtest applet."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

import docking.applets.speedtest.librespeed as librespeed
from docking.applets.speedtest.api import SpeedtestError, run_librespeed
from docking.applets.speedtest.applet import SpeedtestApplet
from docking.applets.speedtest.librespeed import (
    LibrespeedError,
    Server,
    _Counter,
    _download_worker,
    _http_get_drain,
    _http_get_text,
    _open_connection,
    _run_test,
    _upload_worker,
    fetch_server_list,
    parse_server_list,
    ping_jitter,
    run_download,
    run_speedtest,
    run_upload,
    select_fastest,
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
        return SpeedtestApplet(icon_size, config=config or Config())


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

    def test_rejects_non_list_shape(self):
        with pytest.raises(LibrespeedError, match="unexpected"):
            parse_server_list(text='{"not": "a list"}')

    def test_skips_bad_id(self):
        raw = """[
            {"id": "bad", "name": "broken", "server": "//bad.test"},
            {"id": 2, "name": "ok", "server": "//ok.test"}
        ]"""

        servers = parse_server_list(text=raw)

        assert [server.name for server in servers] == ["ok"]


class TestLibrespeedNetworkHelpers:
    def test_fetch_server_list_primary_success(self, monkeypatch):
        monkeypatch.setattr(
            librespeed,
            "_http_get_text",
            lambda **_kwargs: '[{"id": 1, "name": "A", "server": "//a.test"}]',
        )

        servers = fetch_server_list(url="https://servers.test", timeout=1.0)

        assert servers[0].name == "A"

    def test_fetch_server_list_uses_well_known_fallback(self, monkeypatch):
        calls: list[str] = []

        def fake_get_text(*, url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("down")
            return '[{"id": 1, "name": "Fallback", "server": "//b.test"}]'

        monkeypatch.setattr(librespeed, "_http_get_text", fake_get_text)

        servers = fetch_server_list(url="https://servers.test", timeout=1.0)

        assert calls == [
            "https://servers.test",
            "https://servers.test/.well-known/librespeed",
        ]
        assert servers[0].name == "Fallback"

    def test_http_get_text_and_drain_use_user_agent(self, monkeypatch):
        seen = []

        class _Response:
            def __init__(self, payload: bytes) -> None:
                self.payload = payload
                self.read_args = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, *args):
                self.read_args.append(args)
                return self.payload

        response = _Response(b"hello")

        def fake_urlopen(req, timeout):
            seen.append((req.full_url, req.headers["User-agent"], timeout))
            return response

        monkeypatch.setattr(librespeed.urllib.request, "urlopen", fake_urlopen)

        assert _http_get_text(url="https://x.test", timeout=3.0) == "hello"
        _http_get_drain(url="https://x.test/ping", timeout=4.0)

        assert seen == [
            ("https://x.test", librespeed.USER_AGENT, 3.0),
            ("https://x.test/ping", librespeed.USER_AGENT, 4.0),
        ]
        assert response.read_args[-1] == (1,)

    def test_open_connection_variants(self, monkeypatch):
        made = []

        class _Http:
            def __init__(self, host, port=None, timeout=None, **kwargs):
                made.append(("http", host, port, timeout, kwargs))

        class _Https:
            def __init__(self, host, port=None, timeout=None, **kwargs):
                made.append(("https", host, port, timeout, kwargs))

        monkeypatch.setattr(librespeed.http.client, "HTTPConnection", _Http)
        monkeypatch.setattr(librespeed.http.client, "HTTPSConnection", _Https)
        monkeypatch.setattr(librespeed.ssl, "create_default_context", lambda: "ctx")

        assert (
            _open_connection(
                parsed=librespeed.urllib.parse.urlsplit("http://a.test:8080/x"),
                timeout=1.0,
            )
            is not None
        )
        assert (
            _open_connection(
                parsed=librespeed.urllib.parse.urlsplit("https://b.test/y"),
                timeout=2.0,
            )
            is not None
        )
        assert (
            _open_connection(
                parsed=librespeed.urllib.parse.urlsplit("https:///broken"),
                timeout=2.0,
            )
            is None
        )

        assert made[0][:4] == ("http", "a.test", 8080, 1.0)
        assert made[1][:4] == ("https", "b.test", None, 2.0)
        assert made[1][4]["context"] == "ctx"


class TestLibrespeedPingAndSelection:
    def test_ping_jitter_discards_first_sample_and_smooths_jitter(self, monkeypatch):
        times = iter([0.00, 0.10, 0.20, 0.35, 0.40, 0.70, 0.80, 1.15])
        drained: list[str] = []
        server = Server(1, "A", "https://a.test", "dl", "ul", "ping")

        monkeypatch.setattr(librespeed.time, "monotonic", lambda: next(times))
        monkeypatch.setattr(
            librespeed,
            "_http_get_drain",
            lambda *, url, timeout: drained.append(url),
        )

        avg, jitter = ping_jitter(server=server, count=4, timeout=1.0)

        assert drained == ["https://a.test/ping"] * 4
        assert avg == pytest.approx((150 + 300 + 350) / 3)
        assert jitter > 0

    def test_ping_jitter_raises_when_all_samples_fail(self, monkeypatch):
        server = Server(1, "A", "https://a.test", "dl", "ul", "ping")
        monkeypatch.setattr(
            librespeed,
            "_http_get_drain",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("down")),
        )

        with pytest.raises(LibrespeedError, match="no successful"):
            ping_jitter(server=server, count=2)

    def test_select_fastest_picks_lowest_ping_and_skips_failures(self, monkeypatch):
        servers = [
            Server(1, "slow", "https://slow.test", "dl", "ul", "ping"),
            Server(2, "bad", "https://bad.test", "dl", "ul", "ping"),
            Server(3, "fast", "https://fast.test", "dl", "ul", "ping"),
        ]

        def fake_ping(*, server, count, timeout):
            if server.name == "bad":
                raise OSError("down")
            return (20.0 if server.name == "slow" else 5.0, 1.0)

        monkeypatch.setattr(librespeed, "ping_jitter", fake_ping)

        selected, ping, jitter = select_fastest(servers, pool=3)

        assert selected.name == "fast"
        assert ping == 5.0
        assert jitter == 1.0

    def test_select_fastest_errors(self, monkeypatch):
        with pytest.raises(LibrespeedError, match="no servers"):
            select_fastest([])

        monkeypatch.setattr(
            librespeed,
            "ping_jitter",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("down")),
        )
        with pytest.raises(LibrespeedError, match="no server responded"):
            select_fastest([Server(1, "bad", "https://b.test", "dl", "ul", "ping")])


class TestLibrespeedTransfers:
    def test_run_test_counts_bytes(self, monkeypatch):
        now = iter([0.0, 0.2])
        monkeypatch.setattr(librespeed.time, "monotonic", lambda: next(now))
        monkeypatch.setattr(librespeed.time, "sleep", lambda _seconds: None)

        def worker(counter, stop):
            counter.add(250_000)
            stop.set()

        assert _run_test(worker=worker, duration=0.01, concurrency=1) == pytest.approx(
            10.0
        )

    def test_run_download_and_upload_build_expected_workers(self, monkeypatch):
        server = Server(
            1,
            "A",
            "https://a.test",
            "backend/garbage.php?x=1",
            "backend/empty.php",
            "ping",
        )
        captured = []

        def fake_run_test(*, worker, duration, concurrency):
            captured.append((worker, duration, concurrency))
            return 123.0

        monkeypatch.setattr(librespeed, "_run_test", fake_run_test)
        monkeypatch.setattr(librespeed.os, "urandom", lambda size: b"x" * size)

        assert run_download(server=server, duration=2.0, concurrency=4) == 123.0
        assert run_upload(server=server, duration=3.0, concurrency=5) == 123.0

        assert captured[0][1:] == (2.0, 4)
        assert captured[1][1:] == (3.0, 5)

    def test_download_worker_reads_until_response_ends(self, monkeypatch):
        stop = librespeed.threading.Event()
        counter = _Counter()

        class _Response:
            def __init__(self) -> None:
                self.chunks = [b"abc", b"de", b""]

            def read(self, _size):
                chunk = self.chunks.pop(0)
                if not chunk:
                    stop.set()
                return chunk

            def close(self):
                self.closed = True

        class _Conn:
            def __init__(self) -> None:
                self.response = _Response()
                self.closed = False
                self.requests = []

            def request(self, method, path, headers):
                self.requests.append((method, path, headers))

            def getresponse(self):
                return self.response

            def close(self):
                self.closed = True

        conn = _Conn()
        monkeypatch.setattr(librespeed, "_open_connection", lambda **_kwargs: conn)

        _download_worker(
            url="https://a.test/backend/garbage.php?ckSize=100",
            counter=counter,
            stop=stop,
            timeout=1.0,
        )

        assert counter.total == 5
        assert conn.requests[0][0] == "GET"
        assert conn.requests[0][1] == "/backend/garbage.php?ckSize=100"
        assert conn.closed

    def test_download_worker_returns_when_connection_missing(self, monkeypatch):
        stop = librespeed.threading.Event()
        counter = _Counter()
        monkeypatch.setattr(librespeed, "_open_connection", lambda **_kwargs: None)

        _download_worker(
            url="https://a.test/backend/garbage.php",
            counter=counter,
            stop=stop,
            timeout=1.0,
        )

        assert counter.total == 0

    def test_upload_worker_sends_payload_and_counts_bytes(self, monkeypatch):
        stop = librespeed.threading.Event()
        counter = _Counter()

        class _Response:
            def read(self):
                stop.set()
                return b"ok"

            def close(self):
                self.closed = True

        class _Conn:
            def __init__(self) -> None:
                self.headers = []
                self.sent = []
                self.closed = False

            def putrequest(self, *args, **kwargs):
                self.request = (args, kwargs)

            def putheader(self, *args):
                self.headers.append(args)

            def endheaders(self):
                self.ended = True

            def send(self, payload):
                self.sent.append(payload)

            def getresponse(self):
                return _Response()

            def close(self):
                self.closed = True

        conn = _Conn()
        monkeypatch.setattr(librespeed, "_open_connection", lambda **_kwargs: conn)

        _upload_worker(
            url="https://a.test/backend/empty.php?x=1",
            counter=counter,
            stop=stop,
            timeout=1.0,
            payload=b"abcdef",
        )

        assert counter.total == 6
        assert conn.request[0] == ("POST", "/backend/empty.php?x=1")
        assert ("Content-Length", "6") in conn.headers
        assert b"".join(conn.sent) == b"abcdef"
        assert conn.closed


class TestRunSpeedtest:
    def test_orchestrates_speedtest(self, monkeypatch):
        server = Server(7, "Node", "https://n.test", "dl", "ul", "ping")
        monkeypatch.setattr(librespeed, "fetch_server_list", lambda **_k: [server])
        monkeypatch.setattr(
            librespeed,
            "select_fastest",
            lambda servers, timeout: (servers[0], 8.0, 1.5),
        )
        monkeypatch.setattr(librespeed, "run_download", lambda **_k: 100.0)
        monkeypatch.setattr(librespeed, "run_upload", lambda **_k: 20.0)

        result = run_speedtest(duration=0.1, concurrency=1, timeout=2.0)

        assert result.download_mbps == 100.0
        assert result.upload_mbps == 20.0
        assert result.ping_ms == 8.0
        assert result.server_name == "Node"

    def test_errors_when_server_list_empty(self, monkeypatch):
        monkeypatch.setattr(librespeed, "fetch_server_list", lambda **_k: [])

        with pytest.raises(LibrespeedError, match="server list is empty"):
            run_speedtest()


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
        assert "Runs on demand" in text

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
        assert "Runs on demand" in text


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
        assert "Runs on demand" in labels
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
