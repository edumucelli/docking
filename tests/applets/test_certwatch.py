"""Tests for the certwatch applet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
        text = build_tooltip(domains=domains, certs=certs, now=_NOW)
        assert "a.test" in text
        assert "b.test" in text
        assert "5d" in text

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


class TestStatusForCert:
    def test_uses_error_when_present(self):
        cert = _cert("a.test", error="timeout")
        assert status_for_cert(cert=cert, now=_NOW) is CertStatus.ERROR

    def test_uses_days_when_valid(self):
        cert = _cert("a.test", days=3)
        assert status_for_cert(cert=cert, now=_NOW) is CertStatus.CRITICAL
