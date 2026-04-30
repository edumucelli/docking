"""Tests for the certwatch applet."""

from __future__ import annotations

import socket
import ssl
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import docking.applets.certwatch.api as certwatch_api
import docking.applets.certwatch.applet as certwatch_applet_mod
from docking.applets.certwatch.api import fetch_cert
from docking.applets.certwatch.applet import CertwatchApplet
from docking.applets.certwatch.state import (
    CRITICAL_THRESHOLD_DAYS,
    WARN_THRESHOLD_DAYS,
    CertInfo,
    CertStatus,
    CertwatchPrefs,
    DomainPref,
    build_tooltip,
    days_until,
    format_host,
    icon_label,
    min_days,
    parse_host_port,
    prefs_from_mapping,
    prefs_payload,
    status_for,
    status_for_cert,
    tooltip_line,
    worst_status,
)
from docking.core.config import Config

_NOW = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)


def _cert(
    host: str = "example.com",
    port: int = 443,
    *,
    days: int | None = 60,
    error: str | None = None,
) -> CertInfo:
    not_after = None if days is None else _NOW + timedelta(days=days)
    return CertInfo(
        host=host,
        port=port,
        not_after=not_after,
        subject=f"CN={host}",
        issuer="CN=Let's Encrypt",
        error=error,
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
) -> CertwatchApplet:
    with patch("docking.applets.certwatch.applet.BackgroundWorker", _ImmediateWorker):
        return CertwatchApplet(icon_size, config=config)


class TestParseHostPort:
    def test_bare_host_defaults_to_443(self):
        assert parse_host_port("example.com") == DomainPref("example.com", 443)

    def test_host_with_port(self):
        assert parse_host_port("example.com:8443") == DomainPref("example.com", 8443)

    def test_strips_https_scheme(self):
        assert parse_host_port("https://example.com") == DomainPref("example.com", 443)

    def test_strips_http_scheme(self):
        assert parse_host_port("http://example.com") == DomainPref("example.com", 443)

    def test_strips_path(self):
        assert parse_host_port("example.com/foo/bar") == DomainPref("example.com", 443)

    def test_strips_scheme_and_path(self):
        assert parse_host_port("https://example.com:9443/x") == DomainPref(
            "example.com", 9443
        )

    def test_empty_returns_none(self):
        assert parse_host_port("") is None
        assert parse_host_port("   ") is None

    def test_invalid_port_returns_none(self):
        assert parse_host_port("example.com:abc") is None

    def test_out_of_range_port_returns_none(self):
        assert parse_host_port("example.com:0") is None
        assert parse_host_port("example.com:99999") is None


class TestFormatHost:
    def test_hides_default_port(self):
        assert format_host(DomainPref("example.com", 443)) == "example.com"

    def test_shows_non_default_port(self):
        assert format_host(DomainPref("example.com", 8443)) == "example.com:8443"


class TestDaysUntil:
    def test_future(self):
        assert days_until(_NOW + timedelta(days=10), now=_NOW) == 10

    def test_past_is_negative(self):
        assert days_until(_NOW - timedelta(days=3), now=_NOW) == -3

    def test_none_returns_none(self):
        assert days_until(None, now=_NOW) is None

    def test_naive_datetime_coerced_to_utc(self):
        naive = (_NOW + timedelta(days=5)).replace(tzinfo=None)
        assert days_until(naive, now=_NOW) == 5


class TestStatusFor:
    def test_error(self):
        assert status_for(days_left=100, error="timeout") is CertStatus.ERROR

    def test_unknown_when_no_days(self):
        assert status_for(days_left=None, error=None) is CertStatus.UNKNOWN

    def test_expired(self):
        assert status_for(days_left=-1, error=None) is CertStatus.EXPIRED

    def test_critical_under_threshold(self):
        assert (
            status_for(days_left=CRITICAL_THRESHOLD_DAYS - 1, error=None)
            is CertStatus.CRITICAL
        )

    def test_warn_between_thresholds(self):
        assert (
            status_for(days_left=CRITICAL_THRESHOLD_DAYS, error=None) is CertStatus.WARN
        )
        assert (
            status_for(days_left=WARN_THRESHOLD_DAYS - 1, error=None) is CertStatus.WARN
        )

    def test_ok_above_warn(self):
        assert status_for(days_left=WARN_THRESHOLD_DAYS, error=None) is CertStatus.OK


class TestWorstStatus:
    def test_empty_is_unknown(self):
        assert worst_status([], now=_NOW) is CertStatus.UNKNOWN

    def test_picks_worst_among_many(self):
        certs = [
            _cert("a", days=60),
            _cert("b", days=20),
            _cert("c", days=3),
        ]
        assert worst_status(certs, now=_NOW) is CertStatus.CRITICAL

    def test_expired_trumps_critical(self):
        certs = [_cert("a", days=3), _cert("b", days=-1)]
        assert worst_status(certs, now=_NOW) is CertStatus.EXPIRED

    def test_error_not_promoted_over_expired(self):
        certs = [_cert("a", error="timeout"), _cert("b", days=-1)]
        assert worst_status(certs, now=_NOW) is CertStatus.EXPIRED


class TestMinDays:
    def test_none_when_empty(self):
        assert min_days([], now=_NOW) is None

    def test_excludes_errored_certs(self):
        certs = [_cert("a", days=100), _cert("b", error="timeout")]
        assert min_days(certs, now=_NOW) == 100

    def test_picks_closest_to_expiry(self):
        certs = [_cert("a", days=100), _cert("b", days=7)]
        assert min_days(certs, now=_NOW) == 7


class TestIconLabel:
    def test_empty_when_no_certs(self):
        assert icon_label([], now=_NOW) == ""

    def test_days_when_ok(self):
        assert icon_label([_cert("a", days=45)], now=_NOW) == "45"

    def test_x_when_expired(self):
        assert icon_label([_cert("a", days=-1)], now=_NOW) == "X"

    def test_bang_when_error(self):
        assert icon_label([_cert("a", error="timeout")], now=_NOW) == "!"

    def test_question_when_no_data(self):
        assert icon_label([_cert("a", days=None)], now=_NOW) == "?"

    def test_clamps_large_day_counts(self):
        assert icon_label([_cert("a", days=5000)], now=_NOW) == "999"


class TestPrefsRoundTrip:
    def test_none_is_empty(self):
        assert prefs_from_mapping(None) == CertwatchPrefs()

    def test_round_trips(self):
        domains = (
            DomainPref("a.example.com", 443),
            DomainPref("b.example.com", 8443),
        )
        payload = prefs_payload(domains=domains)
        back = prefs_from_mapping(payload)
        assert back.domains == domains

    def test_invalid_port_falls_back_to_default(self):
        raw = {"domains": [{"host": "a.example.com", "port": "bogus"}]}
        prefs = prefs_from_mapping(raw)
        assert prefs.domains == (DomainPref("a.example.com", 443),)

    def test_out_of_range_port_falls_back_to_default(self):
        raw = {"domains": [{"host": "a.example.com", "port": 99999}]}
        prefs = prefs_from_mapping(raw)
        assert prefs.domains == (DomainPref("a.example.com", 443),)

    def test_skips_empty_host(self):
        raw = {"domains": [{"host": "", "port": 443}, {"host": "ok.test"}]}
        prefs = prefs_from_mapping(raw)
        assert prefs.domains == (DomainPref("ok.test", 443),)


class TestTooltip:
    def test_empty_domains_message(self):
        text = build_tooltip(domains=[], certs=[], now=_NOW)
        assert "no domains" in text.lower()

    def test_lists_each_domain(self):
        domains = [DomainPref("a.test", 443), DomainPref("b.test", 443)]
        certs = [_cert("a.test", days=60), _cert("b.test", days=5)]
        text = build_tooltip(
            domains=domains,
            certs=certs,
            now=_NOW,
            updated_at=_NOW,
            cadence_seconds=3600,
        )
        assert "a.test" in text
        assert "b.test" in text
        assert "5d" in text
        assert "Updated:" in text
        assert "Checks every 1 hour" in text

    def test_loading_line_when_cert_missing(self):
        domains = [DomainPref("a.test", 443)]
        text = build_tooltip(domains=domains, certs=[], now=_NOW)
        assert "loading" in text.lower()

    def test_error_line(self):
        text = tooltip_line(_cert("a.test", error="timeout"), now=_NOW)
        assert "error" in text.lower()
        assert "timeout" in text


class TestAppletCreation:
    def test_creates_with_default_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_tooltip_no_domains(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "no domains" in applet.item.name.lower()

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = _make_applet(size)
            assert applet.create_icon(size) is not None

    def test_error_icon_when_all_fetches_failed(self):
        applet = _make_applet()
        applet._domains = [DomainPref("a.test", 443)]
        applet._fetch_error = "boom"

        assert applet.create_icon(48) is not None

    def test_click_opens_add_dialog(self, monkeypatch):
        applet = _make_applet()
        called = []
        monkeypatch.setattr(applet, "_show_add_dialog", lambda: called.append(True))

        applet.on_clicked()

        assert called == [True]


class TestAppletTooltip:
    def test_shows_each_domain(self):
        applet = _make_applet()
        applet._domains = [DomainPref("a.test", 443), DomainPref("b.test", 443)]
        applet._certs = {
            ("a.test", 443): _cert("a.test", days=60),
            ("b.test", 443): _cert("b.test", days=5),
        }
        applet.refresh_tooltip()
        assert "a.test" in applet.item.name
        assert "b.test" in applet.item.name


class TestAppletMenu:
    def test_empty_menu_has_add(self):
        applet = _make_applet()
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert any("Add Domain" in label for label in labels)
        assert not any("Remove" in label for label in labels)

    def test_populated_menu_has_rows_and_remove(self):
        applet = _make_applet()
        applet._domains = [DomainPref("a.test", 443)]
        applet._certs = {("a.test", 443): _cert("a.test", days=60)}
        items = applet.get_menu_items()
        labels = [mi.get_label() for mi in items]
        assert any("a.test" in label for label in labels)
        assert "Checks every 1 hour" in labels
        assert any("Remove" in label for label in labels)
        assert any("Refresh Now" in label for label in labels)

    def test_domain_rows_are_insensitive(self):
        applet = _make_applet()
        applet._domains = [DomainPref("a.test", 443)]
        applet._certs = {("a.test", 443): _cert("a.test", days=60)}
        items = applet.get_menu_items()
        # The first item is the domain row.
        assert not items[0].get_sensitive()


class TestAppletRetry:
    def test_schedules_retry_when_any_cert_errors(self):
        applet = _make_applet()
        applet._domains = [DomainPref("a.test", 443)]
        scheduled: list[int] = []
        with patch(
            "docking.applets.certwatch.applet.GLib.timeout_add_seconds",
            side_effect=lambda s, _fn: (scheduled.append(s), 42)[1],
        ):
            applet._schedule_retry_if_any_error(
                certs=[_cert("a.test", error="timeout")],
            )
        assert scheduled == [300]
        assert applet._retry_timer_id == 42

    def test_no_retry_when_all_ok(self):
        applet = _make_applet()
        scheduled: list[int] = []
        with patch(
            "docking.applets.certwatch.applet.GLib.timeout_add_seconds",
            side_effect=lambda s, _fn: (scheduled.append(s), 42)[1],
        ):
            applet._schedule_retry_if_any_error(certs=[_cert("a.test", days=60)])
        assert scheduled == []
        assert applet._retry_timer_id == 0

    def test_does_not_double_schedule(self):
        applet = _make_applet()
        applet._retry_timer_id = 99
        scheduled: list[int] = []
        with patch(
            "docking.applets.certwatch.applet.GLib.timeout_add_seconds",
            side_effect=lambda s, _fn: (scheduled.append(s), 42)[1],
        ):
            applet._schedule_retry_if_any_error(
                certs=[_cert("a.test", error="timeout")],
            )
        assert scheduled == []
        assert applet._retry_timer_id == 99

    def test_run_retry_clears_timer_and_fetches(self, monkeypatch):
        applet = _make_applet()
        applet._retry_timer_id = 77
        fetch = []
        monkeypatch.setattr(applet, "_fetch_all", lambda: fetch.append(True))

        assert applet._run_retry() is False
        assert applet._retry_timer_id == 0
        assert fetch == [True]


class TestAppletLifecycleAndFetch:
    def test_start_and_stop_timers(self, monkeypatch):
        applet = _make_applet()
        applet._domains = [DomainPref("a.test", 443)]
        added = iter([11, 12])
        removed: list[int] = []
        monkeypatch.setattr(
            certwatch_applet_mod.GLib,
            "timeout_add_seconds",
            lambda *_args: next(added),
        )
        monkeypatch.setattr(
            certwatch_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.start(lambda: None)
        applet._retry_timer_id = 13
        applet.stop()

        assert removed == [11, 12, 13]
        assert applet._timer_id == 0
        assert applet._startup_fetch_timer_id == 0
        assert applet._retry_timer_id == 0

    def test_current_certs_preserves_domain_order(self):
        applet = _make_applet()
        applet._domains = [DomainPref("b.test", 443), DomainPref("a.test", 443)]
        cert_a = _cert("a.test")
        cert_b = _cert("b.test")
        applet._certs = {
            ("a.test", 443): cert_a,
            ("b.test", 443): cert_b,
        }

        assert applet._current_certs() == [cert_b, cert_a]

    def test_tick_and_startup_fetch(self, monkeypatch):
        applet = _make_applet()
        calls: list[str] = []
        monkeypatch.setattr(applet, "_fetch_all", lambda: calls.append("fetch"))

        assert applet._tick() is True
        applet._startup_fetch_timer_id = 99
        assert applet._run_startup_fetch() is False

        assert applet._startup_fetch_timer_id == 0
        assert calls == ["fetch", "fetch"]

    def test_fetch_all_no_domains_is_noop(self):
        applet = _make_applet()
        applet._worker.run = lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("worker should not run")
        )

        applet._fetch_all()

    def test_fetch_all_queues_worker_and_removes_startup_timer(self, monkeypatch):
        applet = _make_applet()
        applet._domains = [DomainPref("a.test", 443)]
        applet._startup_fetch_timer_id = 77
        removed: list[int] = []
        worker_calls: list[dict[str, object]] = []
        monkeypatch.setattr(
            certwatch_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )
        monkeypatch.setattr(
            certwatch_applet_mod,
            "fetch_cert",
            lambda host, port: _cert(host, port),
        )
        applet._worker.run = lambda **kwargs: worker_calls.append(kwargs)

        applet._fetch_all()

        assert removed == [77]
        assert applet._loading is True
        assert worker_calls[0]["name"] == "certwatch-fetch"
        assert worker_calls[0]["fn"]() == [_cert("a.test")]

    def test_fetch_result_ignores_stale_request(self):
        applet = _make_applet()
        applet._fetch_request_id = 2

        assert applet._on_fetch_result(request_id=1, certs=[_cert("a.test")]) is False
        assert applet._certs == {}

    def test_fetch_result_updates_state_and_prunes_stale_certs(self):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet._domains = [DomainPref("a.test", 443)]
        applet._certs = {("old.test", 443): _cert("old.test")}

        assert applet._on_fetch_result(request_id=7, certs=[_cert("a.test")]) is False

        assert ("a.test", 443) in applet._certs
        assert ("old.test", 443) not in applet._certs
        assert applet._last_updated is not None

    def test_fetch_error_ignores_stale_and_schedules_retry(self, monkeypatch):
        applet = _make_applet()
        applet._fetch_request_id = 7
        scheduled: list[int] = []
        monkeypatch.setattr(
            certwatch_applet_mod.GLib,
            "timeout_add_seconds",
            lambda seconds, _fn: scheduled.append(seconds) or 42,
        )

        assert applet._on_fetch_error(request_id=6, exc=RuntimeError("old")) is False
        assert applet._on_fetch_error(request_id=7, exc=RuntimeError("boom")) is False

        assert applet._fetch_error == "boom"
        assert applet._retry_timer_id == 42
        assert scheduled == [300]

    def test_fetch_error_does_not_double_schedule_retry(self, monkeypatch):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet._retry_timer_id = 42
        monkeypatch.setattr(
            certwatch_applet_mod.GLib,
            "timeout_add_seconds",
            lambda *_args: (_ for _ in ()).throw(AssertionError("no schedule")),
        )

        applet._on_fetch_error(request_id=7, exc=RuntimeError())

        assert applet._fetch_error == "RuntimeError"

    def test_log_critical_and_prune_helpers(self):
        applet = _make_applet()
        applet._domains = [DomainPref("keep.test", 443)]
        applet._certs = {
            ("keep.test", 443): _cert("keep.test"),
            ("drop.test", 443): _cert("drop.test"),
        }

        applet._log_critical(certs=[_cert("exp.test", days=-1)])
        applet._prune_stale_certs()

        assert list(applet._certs) == [("keep.test", 443)]


class TestAppletPrefs:
    def test_loads_domains_from_config(self):
        config = Config(
            applet_prefs={
                "certwatch": {
                    "domains": [
                        {"host": "a.test", "port": 443},
                        {"host": "b.test", "port": 8443},
                    ]
                }
            }
        )
        applet = _make_applet(config=config)
        assert applet._domains == [
            DomainPref("a.test", 443),
            DomainPref("b.test", 8443),
        ]

    def test_add_domain_saves(self):
        config = Config(applet_prefs={})
        applet = _make_applet(config=config)
        applet._add_domain(pref=DomainPref("a.test", 443))
        saved = config.applet_prefs["certwatch"]["domains"]
        assert saved == [{"host": "a.test", "port": 443}]

    def test_add_duplicate_is_noop(self):
        config = Config(
            applet_prefs={"certwatch": {"domains": [{"host": "a.test", "port": 443}]}}
        )
        applet = _make_applet(config=config)
        applet._add_domain(pref=DomainPref("a.test", 443))
        assert len(applet._domains) == 1

    def test_remove_domain(self):
        config = Config(
            applet_prefs={
                "certwatch": {
                    "domains": [
                        {"host": "a.test", "port": 443},
                        {"host": "b.test", "port": 443},
                    ]
                }
            }
        )
        applet = _make_applet(config=config)
        applet._remove_domain(pref=DomainPref("a.test", 443))
        assert applet._domains == [DomainPref("b.test", 443)]


class _FakeDialogBox:
    def __init__(self) -> None:
        self.children: list[object] = []

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)


class _FakeDialog:
    def __init__(self, *, response: int) -> None:
        self.response = response
        self.destroyed = False
        self.box = _FakeDialogBox()

    def show_all(self) -> None:
        return

    def run(self) -> int:
        return self.response

    def destroy(self) -> None:
        self.destroyed = True


class _FakeEntry:
    text = ""

    def set_placeholder_text(self, _text: str) -> None:
        return

    def set_activates_default(self, _value: bool) -> None:
        return

    def grab_focus(self) -> None:
        return

    def get_text(self) -> str:
        return self.text


class TestAppletDialog:
    def test_show_add_dialog_adds_valid_domain(self, monkeypatch):
        dialog = _FakeDialog(response=certwatch_applet_mod.Gtk.ResponseType.OK)
        entry = _FakeEntry()
        entry.text = "example.com:8443"
        monkeypatch.setattr(
            certwatch_applet_mod.Gtk, "Dialog", lambda **_kwargs: dialog
        )
        monkeypatch.setattr(certwatch_applet_mod.Gtk, "Entry", lambda: entry)
        monkeypatch.setattr(
            certwatch_applet_mod,
            "prepare_dialog_content",
            lambda **_kwargs: dialog.box,
        )
        monkeypatch.setattr(
            certwatch_applet_mod, "add_cancel_ok_buttons", lambda **_: None
        )
        applet = _make_applet()
        applet._add_domain = MagicMock()

        applet._show_add_dialog()

        applet._add_domain.assert_called_once_with(pref=DomainPref("example.com", 8443))
        assert dialog.destroyed is True

    def test_show_add_dialog_ignores_cancel_and_invalid_domain(self, monkeypatch):
        for response, text in (
            (certwatch_applet_mod.Gtk.ResponseType.CANCEL, "example.com"),
            (certwatch_applet_mod.Gtk.ResponseType.OK, "example.com:bad"),
        ):
            dialog = _FakeDialog(response=response)
            entry = _FakeEntry()
            entry.text = text
            monkeypatch.setattr(
                certwatch_applet_mod.Gtk,
                "Dialog",
                lambda _dialog=dialog, **_kwargs: _dialog,
            )
            monkeypatch.setattr(
                certwatch_applet_mod.Gtk,
                "Entry",
                lambda _entry=entry: _entry,
            )
            monkeypatch.setattr(
                certwatch_applet_mod,
                "prepare_dialog_content",
                lambda _dialog=dialog, **_kwargs: _dialog.box,
            )
            monkeypatch.setattr(
                certwatch_applet_mod,
                "add_cancel_ok_buttons",
                lambda **_: None,
            )
            applet = _make_applet()
            applet._add_domain = MagicMock()

            applet._show_add_dialog()

            applet._add_domain.assert_not_called()
            assert dialog.destroyed is True


class TestStatusForCert:
    def test_uses_error_when_present(self):
        cert = _cert("a.test", error="timeout")
        assert status_for_cert(cert=cert, now=_NOW) is CertStatus.ERROR

    def test_uses_days_when_valid(self):
        cert = _cert("a.test", days=3)
        assert status_for_cert(cert=cert, now=_NOW) is CertStatus.CRITICAL


class TestCertApiParsing:
    def test_parse_cert_date_valid_and_invalid(self):
        parsed = certwatch_api._parse_cert_date("Jun 24 20:14:34 2026 GMT")
        assert parsed == datetime(2026, 6, 24, 20, 14, 34, tzinfo=timezone.utc)
        assert certwatch_api._parse_cert_date("") is None
        assert certwatch_api._parse_cert_date("bad") is None
        assert certwatch_api._parse_cert_date("Nope 24 20:14:34 2026 GMT") is None
        assert certwatch_api._parse_cert_date("Jun x 20:14:34 2026 GMT") is None
        assert certwatch_api._parse_cert_date("Jun 31 20:14:34 2026 GMT") is None

    def test_flatten_name_filters_bad_shapes(self):
        subject = ((("commonName", "example.com"),), "bad", (("O", "Org"),))
        assert certwatch_api._flatten_name(subject) == "commonName=example.com, O=Org"
        assert certwatch_api._flatten_name("bad") == ""


class _FakeRawSocket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeTlsSocket:
    def __init__(self, peer):
        self._peer = peer

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getpeercert(self):
        return self._peer


class _FakeSslContext:
    def __init__(self, peer=None, error: Exception | None = None) -> None:
        self.peer = peer
        self.error = error
        self.server_hostname = ""

    def wrap_socket(self, _raw, *, server_hostname: str):
        self.server_hostname = server_hostname
        if self.error is not None:
            raise self.error
        return _FakeTlsSocket(self.peer)


class TestCertApiFetch:
    def _patch_peer(self, monkeypatch, peer, error: Exception | None = None):
        context = _FakeSslContext(peer=peer, error=error)
        monkeypatch.setattr(
            certwatch_api.ssl, "create_default_context", lambda: context
        )
        monkeypatch.setattr(
            certwatch_api.socket,
            "create_connection",
            lambda *_args, **_kwargs: _FakeRawSocket(),
        )
        return context

    def test_fetch_cert_success(self, monkeypatch):
        peer = {
            "notAfter": "Jun 24 20:14:34 2026 GMT",
            "subject": ((("commonName", "example.com"),),),
            "issuer": ((("commonName", "Test CA"),),),
        }
        context = self._patch_peer(monkeypatch, peer)

        info = fetch_cert(host="example.com", port=443)

        assert info.not_after == datetime(
            2026,
            6,
            24,
            20,
            14,
            34,
            tzinfo=timezone.utc,
        )
        assert info.subject == "commonName=example.com"
        assert info.issuer == "commonName=Test CA"
        assert info.error is None
        assert context.server_hostname == "example.com"

    def test_fetch_cert_reports_verify_failure(self, monkeypatch):
        self._patch_peer(
            monkeypatch,
            peer=None,
            error=ssl.SSLCertVerificationError("bad cert"),
        )

        info = fetch_cert(host="example.com", port=443)

        assert info.error is not None
        assert info.error.startswith("verify failed")

    def test_fetch_cert_reports_timeout(self, monkeypatch):
        monkeypatch.setattr(
            certwatch_api.socket,
            "create_connection",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError),
        )

        assert fetch_cert(host="example.com", port=443).error == "timeout"

    def test_fetch_cert_reports_socket_errors(self, monkeypatch):
        monkeypatch.setattr(
            certwatch_api.socket,
            "create_connection",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(socket.gaierror("no host")),
        )

        assert "no host" in fetch_cert(host="example.com", port=443).error

    def test_fetch_cert_reports_empty_peer(self, monkeypatch):
        self._patch_peer(monkeypatch, peer={})

        assert fetch_cert(host="example.com", port=443).error == "no peer certificate"

    def test_fetch_cert_reports_missing_or_bad_not_after(self, monkeypatch):
        self._patch_peer(monkeypatch, peer={"subject": (), "issuer": ()})
        assert fetch_cert(host="example.com", port=443).error == "missing notAfter"

        self._patch_peer(monkeypatch, peer={"notAfter": "bad"})
        assert (
            "unparseable notAfter"
            in fetch_cert(
                host="example.com",
                port=443,
            ).error
        )
