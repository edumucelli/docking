"""Tests for the Currency FX applet."""

from __future__ import annotations

import datetime as dt
import json
import urllib.parse
from unittest.mock import MagicMock, patch

import docking.applets.currencyfx.applet as currencyfx_applet_mod
import docking.applets.currencyfx.render as currencyfx_render_mod
import docking.applets.currencyfx.state as fx_state
from docking.applets.currencyfx.applet import CurrencyFxApplet
from docking.applets.currencyfx.render import render_icon
from docking.applets.currencyfx.state import (
    LOCAL_SAMPLE_SOURCE,
    ChartInterval,
    CurrencyFxPrefs,
    FxPair,
    FxPoint,
    FxSnapshot,
    append_local_sample,
    build_tooltip,
    chart_interval_days,
    currency_codes_from_units,
    fetch_fx_snapshot,
    fetch_history,
    fetch_live_rate,
    format_change,
    format_rate,
    local_sample_points,
    merge_currency_codes,
    normalize_code,
    normalize_pair,
    pair_rate_from_units,
    parse_history_payload,
    parse_live_rate_payload,
    percent_change,
    prefs_from_mapping,
    prefs_payload,
)
from docking.applets.unitconverter.state import Unit
from docking.core.config import Config


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


def _make_applet(config: Config | None = None) -> CurrencyFxApplet:
    with patch("docking.applets.currencyfx.applet.BackgroundWorker", _ImmediateWorker):
        return CurrencyFxApplet(48, config=config)


def _units() -> tuple[Unit, ...]:
    return (
        Unit("Euro", "EUR", 1.0),
        Unit("US Dollar", "USD", 1.0 / 1.08),
        Unit("British Pound", "GBP", 1.0 / 0.85),
    )


def _snapshot() -> FxSnapshot:
    return FxSnapshot(
        base="EUR",
        quote="USD",
        rate=1.10,
        points=(
            FxPoint(date="2026-04-25", rate=1.08),
            FxPoint(date="2026-04-26", rate=1.09),
            FxPoint(date="2026-04-27", rate=1.10),
        ),
        fetched_at=dt.datetime(2026, 4, 27, tzinfo=dt.timezone.utc),
    )


class TestCurrencyFxState:
    def test_default_prefs(self):
        assert prefs_from_mapping(None) == CurrencyFxPrefs()

    def test_prefs_without_pairs_uses_defaults(self):
        prefs = prefs_from_mapping({"pairs": "bad", "chart_interval": "bad"})
        assert prefs.pairs == (FxPair("EUR", "USD"),)
        assert prefs.active_index == 0
        assert prefs.chart_interval == ChartInterval.WEEK

    def test_normalize_code_and_pair_edges(self):
        assert normalize_code(" usd ", fallback="EUR") == "USD"
        assert normalize_code("bad1", fallback="EUR") == "EUR"
        assert normalize_pair(base="usd", quote="usd") == FxPair("USD", "EUR")

    def test_prefs_loads_added_pairs_and_active_index(self):
        prefs = prefs_from_mapping(
            {
                "pairs": [
                    {"base": "eur", "quote": "usd"},
                    {"base": "eur", "quote": "brl"},
                    {"base": "eur", "quote": "usd"},
                ],
                "active_index": 9,
                "chart_interval": "day",
                "sample_source": LOCAL_SAMPLE_SOURCE,
                "samples": {
                    "EUR/BRL": [{"timestamp": "2026-04-27T10:00:00+00:00", "rate": 5.8}]
                },
            }
        )
        assert prefs.pairs == (FxPair("EUR", "USD"), FxPair("EUR", "BRL"))
        assert prefs.active_index == 1
        assert prefs.chart_interval == ChartInterval.DAY
        assert prefs.samples == {
            "EUR/BRL": (FxPoint("2026-04-27T10:00:00+00:00", 5.8),)
        }

    def test_prefs_handles_bad_active_index_and_bad_samples(self):
        prefs = prefs_from_mapping(
            {
                "pairs": [{"base": "EUR", "quote": "USD"}],
                "active_index": "bad",
                "sample_source": LOCAL_SAMPLE_SOURCE,
                "samples": {
                    1: [{"timestamp": "2026-04-27T10:00:00+00:00", "rate": 5.8}],
                    "bad": [{"timestamp": "2026-04-27T10:00:00+00:00", "rate": 5.8}],
                    "EUR/USD": [
                        "bad",
                        {"timestamp": "", "rate": 1.1},
                        {"timestamp": "2026-04-27T10:00:00+00:00", "rate": -1},
                        {"date": "2026-04-27T10:00:00+00:00", "rate": "1.1"},
                    ],
                },
            }
        )

        assert prefs.active_index == 0
        assert prefs.samples == {
            "EUR/USD": (FxPoint("2026-04-27T10:00:00+00:00", 1.1),)
        }

    def test_prefs_payload_saves_interval_and_samples(self):
        payload = prefs_payload(
            pairs=(FxPair("EUR", "USD"), FxPair("EUR", "BRL")),
            active_index=9,
            chart_interval=ChartInterval.DAY,
            samples={"EUR/BRL": (FxPoint("2026-04-27T10:00:00+00:00", 5.8),)},
        )
        assert payload == {
            "pairs": [
                {"base": "EUR", "quote": "USD"},
                {"base": "EUR", "quote": "BRL"},
            ],
            "active_index": 1,
            "chart_interval": "day",
            "sample_source": LOCAL_SAMPLE_SOURCE,
            "samples": {
                "EUR/BRL": [{"timestamp": "2026-04-27T10:00:00+00:00", "rate": 5.8}]
            },
        }

    def test_samples_payload_skips_empty_and_caps(self):
        points = tuple(FxPoint(f"t{i}", float(i + 1)) for i in range(200))

        payload = fx_state.samples_payload({"EUR/USD": points, "EUR/BRL": ()})

        assert list(payload) == ["EUR/USD"]
        assert len(payload["EUR/USD"]) == fx_state.LOCAL_SAMPLE_MAX_PER_PAIR
        assert payload["EUR/USD"][0]["timestamp"] == "t8"

    def test_prefs_ignore_samples_from_old_source(self):
        prefs = prefs_from_mapping(
            {
                "pairs": [{"base": "EUR", "quote": "BRL"}],
                "chart_interval": "day",
                "samples": {
                    "EUR/BRL": [{"timestamp": "2026-04-27T10:00:00+00:00", "rate": 5.8}]
                },
            }
        )
        assert prefs.samples == {}

    def test_chart_interval_days(self):
        assert chart_interval_days(ChartInterval.DAY) == 1
        assert chart_interval_days(ChartInterval.WEEK) == 7
        assert chart_interval_days(ChartInterval.MONTH) == 30
        assert chart_interval_days("invalid") == 7

    def test_local_samples_prune_to_recent_pair_points(self):
        now = dt.datetime(2026, 4, 27, 12, tzinfo=dt.timezone.utc)
        samples = append_local_sample(
            samples={},
            base="EUR",
            quote="BRL",
            rate=5.8,
            now=now - dt.timedelta(hours=2),
        )
        samples = append_local_sample(
            samples=samples,
            base="EUR",
            quote="BRL",
            rate=5.9,
            now=now,
        )
        points = local_sample_points(
            samples=samples,
            base="EUR",
            quote="BRL",
            now=now,
        )
        assert points == (
            FxPoint("2026-04-27T10:00:00+00:00", 5.8),
            FxPoint("2026-04-27T12:00:00+00:00", 5.9),
        )

    def test_local_samples_drop_bad_old_and_duplicate_points(self):
        now = dt.datetime(2026, 4, 27, 12, tzinfo=dt.timezone.utc)
        samples = {
            "EUR/USD": (
                FxPoint("bad", 1.0),
                FxPoint("2026-04-25T10:00:00+00:00", 1.1),
                FxPoint("2026-04-27T12:00:00+00:00", 1.2),
            )
        }

        assert local_sample_points(
            samples=samples,
            base="EUR",
            quote="USD",
            now=now,
        ) == (FxPoint("2026-04-27T12:00:00+00:00", 1.2),)

        unchanged = append_local_sample(
            samples=samples,
            base="EUR",
            quote="USD",
            rate=0,
            now=now,
        )
        assert unchanged == samples

        updated = append_local_sample(
            samples={"EUR/USD": (FxPoint("2026-04-27T12:00:00+00:00", 1.2),)},
            base="EUR",
            quote="USD",
            rate=1.3,
            now=now,
        )
        assert updated["EUR/USD"] == (FxPoint("2026-04-27T12:00:00+00:00", 1.3),)

    def test_currency_codes_from_units(self):
        assert currency_codes_from_units(_units()) == ("EUR", "GBP", "USD")

    def test_merge_currency_codes_keeps_defaults_first(self):
        codes = merge_currency_codes(("ZAR", "USD", "EUR"))
        assert codes[:2] == ("EUR", "USD")
        assert codes[-1] == "ZAR"

    def test_pair_rate_from_units(self):
        result = pair_rate_from_units(units=_units(), base="USD", quote="GBP")
        assert result is not None
        assert abs(result - (0.85 / 1.08)) < 0.001

    def test_pair_rate_same_currency(self):
        assert pair_rate_from_units(units=(), base="USD", quote="USD") == 1.0

    def test_pair_rate_unknown_currency(self):
        assert pair_rate_from_units(units=_units(), base="USD", quote="XXX") is None

    def test_parse_history_payload(self):
        data = {
            "rates": [
                {"date": "2026-04-26", "rate": 1.09},
                {"date": "2026-04-25", "rate": 1.08},
            ]
        }
        points = parse_history_payload(data=data)
        assert points == (
            FxPoint(date="2026-04-25", rate=1.08),
            FxPoint(date="2026-04-26", rate=1.09),
        )

    def test_parse_history_payload_ignores_invalid_shapes(self):
        assert parse_history_payload(data=[]) == ()
        assert parse_history_payload(data={"rates": "bad"}) == ()
        assert (
            parse_history_payload(
                data={"rates": ["bad", {"date": "2026-04-27", "rate": "bad"}]}
            )
            == ()
        )

    def test_parse_live_rate_payload(self):
        point = parse_live_rate_payload(
            data={
                "base": "EUR",
                "target": "BRL",
                "rate": 5.84,
                "timestamp": "2026-04-27T12:55:29.819Z",
            },
            base="EUR",
            quote="BRL",
        )
        assert point == FxPoint(
            date="2026-04-27T12:55:29.819Z",
            rate=5.84,
        )

    def test_parse_live_rate_payload_rejects_bad_shapes(self):
        assert parse_live_rate_payload(data=[], base="EUR", quote="USD") is None
        assert (
            parse_live_rate_payload(
                data={"base": "EUR", "target": "BRL", "rate": 5.8},
                base="EUR",
                quote="USD",
            )
            is None
        )

    def test_fetch_live_rate_parses_response(self, monkeypatch):
        fake_data = json.dumps(
            {
                "base": "EUR",
                "target": "BRL",
                "rate": 5.84,
                "timestamp": "2026-04-27T12:55:29.819Z",
            }
        ).encode()
        seen_urls: list[str] = []

        class FakeResp:
            def read(self):
                return fake_data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        monkeypatch.setattr(
            fx_state.urllib.request,
            "urlopen",
            lambda req, **_kwargs: seen_urls.append(req.full_url) or FakeResp(),
        )

        point = fetch_live_rate(base="EUR", quote="BRL")

        assert point == FxPoint("2026-04-27T12:55:29.819Z", 5.84)
        assert seen_urls == ["https://fxapi.app/api/EUR/BRL.json"]

    def test_fetch_live_rate_same_pair_and_failure(self, monkeypatch):
        urlopen = MagicMock(side_effect=OSError("down"))
        monkeypatch.setattr(fx_state.urllib.request, "urlopen", urlopen)

        same = fetch_live_rate(base="USD", quote="USD")
        failed = fetch_live_rate(base="EUR", quote="USD")

        assert same == FxPoint(same.date, 1.0)
        assert failed is None
        urlopen.assert_called_once()

    def test_fetch_history_parses_response(self, monkeypatch):
        fake_data = json.dumps(
            {"rates": [{"date": "2026-04-27", "rate": 1.10}]}
        ).encode()
        seen_urls: list[str] = []

        class FakeResp:
            def read(self):
                return fake_data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        monkeypatch.setattr(
            fx_state.urllib.request,
            "urlopen",
            lambda req, **_kwargs: seen_urls.append(req.full_url) or FakeResp(),
        )

        points = fetch_history(
            base="EUR",
            quote="USD",
            chart_interval=ChartInterval.WEEK,
            today=dt.date(2026, 4, 27),
        )

        assert points == (FxPoint(date="2026-04-27", rate=1.10),)
        parsed = urllib.parse.urlparse(seen_urls[0])
        query = urllib.parse.parse_qs(parsed.query)
        assert parsed.path == "/api/history/EUR/USD.json"
        assert query["from"] == ["2026-04-21"]
        assert query["to"] == ["2026-04-27"]

    def test_fetch_history_day_uses_local_cache_only(self, monkeypatch):
        urlopen = MagicMock()
        monkeypatch.setattr(fx_state.urllib.request, "urlopen", urlopen)

        assert fetch_history(base="EUR", quote="USD", chart_interval="day") == ()
        urlopen.assert_not_called()

    def test_fetch_history_same_currency_returns_flat_point(self):
        assert fetch_history(
            base="USD",
            quote="USD",
            chart_interval=ChartInterval.WEEK,
            today=dt.date(2026, 4, 27),
        ) == (FxPoint("2026-04-27", 1.0),)

    def test_fetch_history_month_requests_30_days(self, monkeypatch):
        fake_data = json.dumps({"rates": []}).encode()
        seen_urls: list[str] = []

        class FakeResp:
            def read(self):
                return fake_data

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        monkeypatch.setattr(
            fx_state.urllib.request,
            "urlopen",
            lambda req, **_kwargs: seen_urls.append(req.full_url) or FakeResp(),
        )

        fetch_history(
            base="EUR",
            quote="USD",
            chart_interval=ChartInterval.MONTH,
            today=dt.date(2026, 4, 27),
        )

        query = urllib.parse.parse_qs(urllib.parse.urlparse(seen_urls[0]).query)
        assert query["from"] == ["2026-03-29"]
        assert query["to"] == ["2026-04-27"]

    def test_fetch_history_returns_empty_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            fx_state.urllib.request,
            "urlopen",
            MagicMock(side_effect=OSError("no network")),
        )
        assert fetch_history(base="EUR", quote="USD") == ()

    def test_fetch_snapshot_prefers_live_rate(self, monkeypatch):
        monkeypatch.setattr(fx_state, "fetch_currency_rates", lambda: _units())
        monkeypatch.setattr(
            fx_state,
            "fetch_live_rate",
            lambda **_kwargs: FxPoint("2026-04-27T12:55:29.819Z", 1.09),
        )
        monkeypatch.setattr(
            fx_state,
            "fetch_history",
            lambda **_kwargs: (FxPoint(date=dt.date.today().isoformat(), rate=1.07),),
        )

        snapshot, codes = fetch_fx_snapshot(base="EUR", quote="USD")

        assert snapshot is not None
        assert snapshot.rate == 1.09
        assert snapshot.points[-1] == FxPoint(dt.date.today().isoformat(), 1.09)
        assert codes == ("EUR", "GBP", "USD")

    def test_fetch_snapshot_falls_back_to_unitconverter_rate(self, monkeypatch):
        monkeypatch.setattr(fx_state, "fetch_currency_rates", lambda: _units())
        monkeypatch.setattr(fx_state, "fetch_live_rate", lambda **_kwargs: None)
        monkeypatch.setattr(
            fx_state,
            "fetch_history",
            lambda **_kwargs: (FxPoint(date="2026-04-26", rate=1.07),),
        )

        snapshot, codes = fetch_fx_snapshot(base="EUR", quote="USD")

        assert snapshot is not None
        assert abs(snapshot.rate - 1.08) < 0.001
        assert codes == ("EUR", "GBP", "USD")

    def test_fetch_snapshot_day_uses_no_history_points(self, monkeypatch):
        monkeypatch.setattr(fx_state, "fetch_currency_rates", lambda: _units())
        monkeypatch.setattr(
            fx_state,
            "fetch_live_rate",
            lambda **_kwargs: FxPoint("2026-04-27T12:55:29.819Z", 1.09),
        )
        fetch_history_mock = MagicMock(return_value=(FxPoint("2026-04-27", 1.07),))
        monkeypatch.setattr(fx_state, "fetch_history", fetch_history_mock)

        snapshot, _codes = fetch_fx_snapshot(
            base="EUR",
            quote="USD",
            chart_interval=ChartInterval.DAY,
        )

        assert snapshot is not None
        assert snapshot.rate == 1.09
        assert snapshot.points == ()
        assert snapshot.fetched_at == dt.datetime(
            2026,
            4,
            27,
            12,
            55,
            29,
            819000,
            tzinfo=dt.timezone.utc,
        )
        fetch_history_mock.assert_not_called()

    def test_fetch_snapshot_falls_back_to_history_rate(self, monkeypatch):
        monkeypatch.setattr(fx_state, "fetch_currency_rates", lambda: None)
        monkeypatch.setattr(fx_state, "fetch_live_rate", lambda **_kwargs: None)
        monkeypatch.setattr(
            fx_state,
            "fetch_history",
            lambda **_kwargs: (FxPoint(date="2026-04-27", rate=1.07),),
        )

        snapshot, codes = fetch_fx_snapshot(base="EUR", quote="USD")

        assert snapshot is not None
        assert snapshot.rate == 1.07
        assert codes == ()

    def test_fetch_snapshot_returns_none_when_no_rate_available(self, monkeypatch):
        monkeypatch.setattr(fx_state, "fetch_currency_rates", lambda: ())
        monkeypatch.setattr(fx_state, "fetch_live_rate", lambda **_kwargs: None)
        monkeypatch.setattr(fx_state, "fetch_history", lambda **_kwargs: ())

        snapshot, codes = fetch_fx_snapshot(base="EUR", quote="XXX")

        assert snapshot is None
        assert codes == ()

    def test_fetch_snapshot_appends_current_point_when_history_is_old(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr(fx_state, "fetch_currency_rates", lambda: _units())
        monkeypatch.setattr(
            fx_state,
            "fetch_live_rate",
            lambda **_kwargs: FxPoint("2026-04-27T12:00:00+00:00", 1.09),
        )
        monkeypatch.setattr(
            fx_state,
            "fetch_history",
            lambda **_kwargs: (FxPoint(date="2026-04-26", rate=1.07),),
        )

        snapshot, _codes = fetch_fx_snapshot(base="EUR", quote="USD")

        assert snapshot is not None
        assert snapshot.points[-2:] == (
            FxPoint("2026-04-26", 1.07),
            FxPoint(dt.date.today().isoformat(), 1.09),
        )

    def test_format_rate(self):
        assert format_rate(123.456) == "123.46"
        assert format_rate(1.23456) == "1.2346"
        assert format_rate(0.1234567) == "0.123457"
        assert format_rate(None) == "-"

    def test_format_change(self):
        points = (FxPoint("a", 1.0), FxPoint("b", 1.1))
        assert format_change(points) == "+10.00%"
        assert format_change((FxPoint("a", 1.0),)) == "n/a"
        assert percent_change((FxPoint("a", 0), FxPoint("b", 1.1))) is None

    def test_build_tooltip(self):
        tooltip = build_tooltip(
            base="EUR",
            quote="USD",
            snapshot=_snapshot(),
            fetch_failed=False,
            cadence_seconds=15 * 60,
        )
        assert "EUR/USD" in tooltip
        assert "1 EUR = 1.1 USD" in tooltip
        assert "Updated:" in tooltip
        assert "Updates every 15 minutes" in tooltip

    def test_build_tooltip_discloses_day_sample_cadence(self):
        tooltip = build_tooltip(
            base="EUR",
            quote="USD",
            snapshot=_snapshot(),
            fetch_failed=False,
            chart_interval=ChartInterval.DAY,
            cadence_seconds=15 * 60,
        )

        assert "Day samples every 15 minutes" in tooltip

    def test_build_tooltip_empty_and_error_states(self):
        text = build_tooltip(
            base="EUR",
            quote="USD",
            snapshot=None,
            loading=False,
            fetch_failed=True,
            error="down",
            cadence_seconds=15 * 60,
        )

        assert "EUR/USD" in text
        assert "down" in text


class TestCurrencyFxRender:
    def test_renders_snapshot_at_various_sizes(self):
        for size in (32, 48, 64):
            pixbuf = render_icon(
                size=size,
                snapshot=_snapshot(),
                base="EUR",
                quote="USD",
                pulse_phase=0.5,
            )
            assert pixbuf is not None
            assert pixbuf.get_width() == size
            assert pixbuf.get_height() == size

    def test_renders_empty_state(self):
        pixbuf = render_icon(
            size=48,
            snapshot=None,
            base="EUR",
            quote="USD",
            fetch_failed=True,
        )
        assert pixbuf is not None

    def test_quote_label_uses_shared_icon_label_for_long_codes(self, monkeypatch):
        labels: list[tuple[str, float | None]] = []

        def draw_label(*, cr, text: str, size: int, max_width=None) -> None:
            labels.append((text, max_width))

        monkeypatch.setattr(currencyfx_render_mod, "draw_icon_label", draw_label)

        pixbuf = render_icon(
            size=48,
            snapshot=None,
            base="EUR",
            quote="LONGCODE",
        )

        assert pixbuf is not None
        assert labels == [("LONGCODE", 48 * 0.78)]


class TestCurrencyFxApplet:
    def test_creates_with_icon_and_tooltip(self):
        applet = _make_applet()
        assert applet.item.icon is not None
        assert "EUR/USD" in applet.item.name

    def test_click_opens_pair_dialog_and_single_scroll_is_noop(self, monkeypatch):
        applet = _make_applet()
        called = []
        monkeypatch.setattr(applet, "_show_pair_dialog", lambda: called.append(True))

        applet.on_clicked()
        applet.on_scroll(direction_up=False)

        assert called == [True]
        assert applet._active_index == 0

    def test_loads_prefs_from_config(self):
        timestamp = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
        ).isoformat()
        config = Config(
            applet_prefs={
                "currencyfx": {
                    "pairs": [
                        {"base": "GBP", "quote": "USD"},
                        {"base": "EUR", "quote": "BRL"},
                    ],
                    "active_index": 1,
                    "chart_interval": "day",
                    "sample_source": LOCAL_SAMPLE_SOURCE,
                    "samples": {
                        "EUR/BRL": [
                            {
                                "timestamp": timestamp,
                                "rate": 5.8,
                            }
                        ]
                    },
                }
            }
        )
        applet = _make_applet(config=config)
        assert applet._base == "EUR"
        assert applet._quote == "BRL"
        assert applet._pairs == [FxPair("GBP", "USD"), FxPair("EUR", "BRL")]
        assert applet._chart_interval == ChartInterval.DAY
        assert applet._snapshot is not None
        assert applet._snapshot.points == (FxPoint(timestamp, 5.8),)

    def test_start_schedules_refresh_and_startup_fetch(self, monkeypatch):
        add = MagicMock(side_effect=[11, 12])
        monkeypatch.setattr(currencyfx_applet_mod.GLib, "timeout_add_seconds", add)
        pulse_add = MagicMock()
        monkeypatch.setattr(currencyfx_applet_mod.GLib, "timeout_add", pulse_add)
        applet = _make_applet()

        applet.start(lambda: None)

        assert applet._timer_id == 11
        assert applet._startup_fetch_timer_id == 12
        assert add.call_count == 2
        pulse_add.assert_not_called()

    def test_stop_removes_all_timers(self, monkeypatch):
        applet = _make_applet()
        applet._timer_id = 11
        applet._startup_fetch_timer_id = 12
        applet._pulse_timer_id = 13
        removed: list[int] = []
        monkeypatch.setattr(
            currencyfx_applet_mod.GLib,
            "source_remove",
            lambda timer_id: removed.append(timer_id),
        )

        applet.stop()

        assert removed == [11, 12, 13]
        assert applet._timer_id == 0
        assert applet._startup_fetch_timer_id == 0
        assert applet._pulse_timer_id == 0

    def test_pulse_timer_starts_when_chart_dot_visible(self, monkeypatch):
        add_seconds = MagicMock(side_effect=[11, 12])
        pulse_add = MagicMock(return_value=42)
        monkeypatch.setattr(
            currencyfx_applet_mod.GLib,
            "timeout_add_seconds",
            add_seconds,
        )
        monkeypatch.setattr(currencyfx_applet_mod.GLib, "timeout_add", pulse_add)
        applet = _make_applet()
        applet._snapshot = _snapshot()

        applet.start(lambda: None)

        assert applet._pulse_timer_id == 42
        pulse_add.assert_called_once()

    def test_pulse_timer_stops_without_chart_dot(self, monkeypatch):
        remove = MagicMock()
        monkeypatch.setattr(currencyfx_applet_mod.GLib, "source_remove", remove)
        applet = _make_applet()
        applet._notify = lambda: None
        applet._pulse_timer_id = 42
        applet._pulse_phase = 0.5
        applet._snapshot = None

        applet._ensure_pulse_timer()

        remove.assert_called_once_with(42)
        assert applet._pulse_timer_id == 0
        assert applet._pulse_phase == 0.0

    def test_pulse_tick_advances_phase_and_repaints(self):
        applet = _make_applet()
        applet._snapshot = _snapshot()
        applet._notify = MagicMock()

        assert applet._pulse_tick() is True

        assert applet._pulse_phase > 0.0
        applet._notify.assert_called_once()

    def test_fetch_async_queues_worker(self, monkeypatch):
        remove = MagicMock()
        monkeypatch.setattr(currencyfx_applet_mod.GLib, "source_remove", remove)
        applet = _make_applet()
        applet._startup_fetch_timer_id = 42
        applet._worker.run = MagicMock()

        applet._fetch_async()

        remove.assert_called_once_with(42)
        assert applet._startup_fetch_timer_id == 0
        assert applet._fetch_request_id == 1
        assert applet._worker.run.call_args.kwargs["name"] == "currencyfx-fetch"

    def test_tick_and_startup_fetch(self, monkeypatch):
        applet = _make_applet()
        calls: list[str] = []
        monkeypatch.setattr(applet, "_fetch_async", lambda: calls.append("fetch"))

        assert applet._tick() is True
        applet._startup_fetch_timer_id = 99
        assert applet._run_startup_fetch() is False

        assert applet._startup_fetch_timer_id == 0
        assert calls == ["fetch", "fetch"]

    def test_fetch_result_updates_snapshot_and_codes(self):
        applet = _make_applet()
        applet._fetch_request_id = 7

        assert (
            applet._on_fetch_result(
                request_id=7,
                snapshot=_snapshot(),
                codes=("EUR", "USD", "ZAR"),
            )
            is False
        )

        assert applet._snapshot is not None
        assert applet._fetch_failed is False
        assert "ZAR" in applet._available_codes
        assert "EUR/USD" in applet._samples

    def test_fetch_result_ignores_stale_and_handles_empty_snapshot(self):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet.present = MagicMock()

        assert (
            applet._on_fetch_result(request_id=6, snapshot=_snapshot(), codes=())
            is False
        )
        assert applet._snapshot is None
        applet.present.assert_not_called()

        assert applet._on_fetch_result(request_id=7, snapshot=None, codes=()) is False
        assert applet._fetch_failed is True
        assert applet._fetch_error == "No rate data"

    def test_day_fetch_result_renders_local_samples(self):
        applet = _make_applet()
        applet._chart_interval = ChartInterval.DAY
        applet._fetch_request_id = 7

        assert (
            applet._on_fetch_result(
                request_id=7,
                snapshot=_snapshot(),
                codes=("EUR", "USD"),
            )
            is False
        )

        assert applet._snapshot is not None
        assert len(applet._snapshot.points) == 1
        assert applet._snapshot.points[0].rate == 1.10

    def test_fetch_error_marks_failed(self):
        applet = _make_applet()
        applet._fetch_request_id = 7

        assert applet._on_fetch_error(request_id=7, exc=OSError("no network")) is False

        assert applet._snapshot is None
        assert applet._fetch_failed is True

    def test_fetch_error_ignores_stale_and_uses_exception_name(self):
        applet = _make_applet()
        applet._fetch_request_id = 7
        applet.present = MagicMock()

        assert applet._on_fetch_error(request_id=6, exc=RuntimeError("old")) is False
        applet.present.assert_not_called()

        assert applet._on_fetch_error(request_id=7, exc=RuntimeError()) is False
        assert applet._fetch_error == "RuntimeError"

    def test_add_pair_appends_saves_and_fetches(self):
        applet = _make_applet()
        applet.save_prefs = MagicMock()
        applet._fetch_async = MagicMock()

        applet._add_pair("GBP", "USD")

        assert applet._base == "GBP"
        assert applet._quote == "USD"
        assert applet._pairs == [FxPair("EUR", "USD"), FxPair("GBP", "USD")]
        applet.save_prefs.assert_called_once()
        applet._fetch_async.assert_called_once()

    def test_add_pair_activates_existing_pair(self):
        applet = _make_applet()
        applet._pairs = [FxPair("EUR", "USD"), FxPair("EUR", "BRL")]
        applet._active_index = 0
        applet.save_prefs = MagicMock()
        applet._fetch_async = MagicMock()

        applet._add_pair("EUR", "BRL")

        assert applet._active_index == 1
        assert applet._pairs == [FxPair("EUR", "USD"), FxPair("EUR", "BRL")]
        applet.save_prefs.assert_called_once()
        applet._fetch_async.assert_called_once()

    def test_add_pair_ignores_same_code(self):
        applet = _make_applet()
        applet.save_prefs = MagicMock()
        applet._fetch_async = MagicMock()

        applet._add_pair("USD", "USD")

        applet.save_prefs.assert_not_called()
        applet._fetch_async.assert_not_called()

    def test_scroll_cycles_added_pairs(self):
        applet = _make_applet()
        applet._pairs = [
            FxPair("EUR", "USD"),
            FxPair("EUR", "BRL"),
            FxPair("GBP", "USD"),
        ]
        applet._active_index = 0
        applet.save_prefs = MagicMock()
        applet._fetch_async = MagicMock()

        applet.on_scroll(direction_up=False)
        assert applet._active_index == 1
        applet.on_scroll(direction_up=True)
        assert applet._active_index == 0

    def test_activate_pair_index_guards_empty_and_same_index(self):
        applet = _make_applet()
        applet._pairs = []
        applet._activate_pair_index(0)

        applet._pairs = [FxPair("EUR", "USD")]
        applet._active_index = 0
        applet._pair_changed = MagicMock()
        applet._activate_pair_index(9)

        applet._pair_changed.assert_not_called()

    def test_remove_active_pair_keeps_cycle_valid(self):
        applet = _make_applet()
        applet._pairs = [FxPair("EUR", "USD"), FxPair("EUR", "BRL")]
        applet._active_index = 1
        applet.save_prefs = MagicMock()
        applet._fetch_async = MagicMock()

        applet._remove_active_pair()

        assert applet._pairs == [FxPair("EUR", "USD")]
        assert applet._active_index == 0
        applet.save_prefs.assert_called_once()
        applet._fetch_async.assert_called_once()

    def test_remove_active_pair_ignores_single_pair(self):
        applet = _make_applet()
        applet.save_prefs = MagicMock()

        applet._remove_active_pair()

        applet.save_prefs.assert_not_called()

    def test_interval_selection_saves_and_fetches(self):
        applet = _make_applet()
        applet.save_prefs = MagicMock()
        applet._fetch_async = MagicMock()
        widget = MagicMock()
        widget.get_active.return_value = True

        applet._on_interval_selected(widget=widget, interval=ChartInterval.MONTH)

        assert applet._chart_interval == ChartInterval.MONTH
        applet.save_prefs.assert_called_once()
        applet._fetch_async.assert_called_once()

    def test_interval_selection_ignores_current_interval(self):
        applet = _make_applet()
        applet.save_prefs = MagicMock()
        widget = MagicMock()
        widget.get_active.return_value = True

        applet._on_interval_selected(widget=widget, interval=ChartInterval.WEEK)

        applet.save_prefs.assert_not_called()

    def test_interval_selection_ignores_inactive_widget(self):
        applet = _make_applet()
        applet.save_prefs = MagicMock()
        applet._fetch_async = MagicMock()
        widget = MagicMock()
        widget.get_active.return_value = False

        applet._on_interval_selected(widget=widget, interval=ChartInterval.DAY)

        assert applet._chart_interval == ChartInterval.WEEK
        applet.save_prefs.assert_not_called()
        applet._fetch_async.assert_not_called()

    def test_menu_header_includes_snapshot_rate(self):
        applet = _make_applet()
        applet._snapshot = _snapshot()
        assert "EUR/USD" in applet._menu_header()
        assert "1.1" in applet._menu_header()

    def test_menu_discloses_update_cadence(self):
        applet = _make_applet()
        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Updates every 15 minutes" in labels

    def test_menu_with_multiple_pairs_has_pair_controls(self):
        applet = _make_applet()
        applet._pairs = [FxPair("EUR", "USD"), FxPair("EUR", "BRL")]

        labels = [item.get_label() for item in applet.get_menu_items()]

        assert "Added Pairs" in labels
        assert "Remove Current Pair" in labels

    def test_pair_changed_uses_day_cache_snapshot(self):
        applet = _make_applet()
        applet._chart_interval = ChartInterval.DAY
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        applet._samples = {"EUR/USD": (FxPoint(now, 1.2),)}
        applet._fetch_async = MagicMock()

        applet._pair_changed()

        assert applet._snapshot is not None
        assert applet._snapshot.rate == 1.2
        applet._fetch_async.assert_called_once()

    def test_local_snapshot_helpers(self):
        applet = _make_applet()
        assert applet._snapshot_from_local_samples() is None

        fetched = _snapshot()
        applet._samples = {"EUR/USD": (FxPoint(fetched.fetched_at.isoformat(), 1.10),)}
        with_local = applet._snapshot_with_local_samples(snapshot=fetched)

        assert with_local.points == (FxPoint(fetched.fetched_at.isoformat(), 1.10),)
        assert with_local.fetched_at == fetched.fetched_at

    def test_currency_combo_and_pair_codes(self, monkeypatch):
        monkeypatch.setattr(currencyfx_applet_mod.Gtk, "ComboBoxText", _FakeCombo)
        applet = _make_applet()
        applet._available_codes = ("EUR", "USD")
        combo = applet._currency_combo(active="BRL")

        assert combo.get_active_text() == "EUR"
        assert applet._pair_codes() == ("EUR", "USD")


class _FakeBox:
    def __init__(self) -> None:
        self.children: list[object] = []

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)


class _FakeDialog:
    def __init__(self, *, response: int) -> None:
        self.response = response
        self.box = _FakeBox()
        self.destroyed = False

    def show_all(self) -> None:
        return

    def run(self) -> int:
        return self.response

    def destroy(self) -> None:
        self.destroyed = True


class _FakeCombo:
    active_text = ""

    def __init__(self) -> None:
        self.values: list[str] = []
        self.active = 0

    def append_text(self, text: str) -> None:
        self.values.append(text)

    def set_active(self, index: int) -> None:
        self.active = index

    def get_active_text(self) -> str:
        return self.active_text or self.values[self.active]

    def grab_focus(self) -> None:
        return


class _FakeLabel:
    def __init__(self, label: str = "") -> None:
        self.label = label


class TestCurrencyFxDialog:
    def test_show_pair_dialog_adds_selected_pair(self, monkeypatch):
        dialog = _FakeDialog(response=currencyfx_applet_mod.Gtk.ResponseType.OK)
        combos: list[_FakeCombo] = []

        def combo_factory():
            combo = _FakeCombo()
            combos.append(combo)
            return combo

        monkeypatch.setattr(currencyfx_applet_mod.Gtk, "Dialog", lambda **_: dialog)
        monkeypatch.setattr(currencyfx_applet_mod.Gtk, "ComboBoxText", combo_factory)
        monkeypatch.setattr(currencyfx_applet_mod.Gtk, "Label", _FakeLabel)
        monkeypatch.setattr(
            currencyfx_applet_mod,
            "prepare_dialog_content",
            lambda **_: dialog.box,
        )
        monkeypatch.setattr(
            currencyfx_applet_mod, "add_cancel_ok_buttons", lambda **_: None
        )
        applet = _make_applet()
        applet._available_codes = ("EUR", "USD", "BRL")
        applet._add_pair = MagicMock()

        applet._show_pair_dialog()

        applet._add_pair.assert_called_once_with("EUR", "USD")
        assert dialog.destroyed is True

    def test_show_pair_dialog_ignores_cancel(self, monkeypatch):
        dialog = _FakeDialog(response=currencyfx_applet_mod.Gtk.ResponseType.CANCEL)
        monkeypatch.setattr(currencyfx_applet_mod.Gtk, "Dialog", lambda **_: dialog)
        monkeypatch.setattr(currencyfx_applet_mod.Gtk, "ComboBoxText", _FakeCombo)
        monkeypatch.setattr(currencyfx_applet_mod.Gtk, "Label", _FakeLabel)
        monkeypatch.setattr(
            currencyfx_applet_mod,
            "prepare_dialog_content",
            lambda **_: dialog.box,
        )
        monkeypatch.setattr(
            currencyfx_applet_mod, "add_cancel_ok_buttons", lambda **_: None
        )
        applet = _make_applet()
        applet._add_pair = MagicMock()

        applet._show_pair_dialog()

        applet._add_pair.assert_not_called()
