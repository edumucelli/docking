"""Tests for the Crypto applet."""

from __future__ import annotations

import datetime as dt
from unittest.mock import patch

import docking.applets.crypto.applet as crypto_applet_mod
import docking.applets.crypto.state as crypto_state_mod
from docking.applets.crypto.applet import CryptoApplet
from docking.applets.crypto.render import render_icon
from docking.applets.crypto.state import (
    AssetType,
    ChartInterval,
    CryptoAsset,
    CryptoPoint,
    CryptoSnapshot,
    append_local_sample,
    build_tooltip,
    format_change,
    format_price,
    parse_coin_market_payload,
    parse_market_chart_payload,
    parse_nft_payload,
    prefs_from_mapping,
    prefs_payload,
)

BTC = CryptoAsset(AssetType.COIN, "bitcoin", "BTC", "Bitcoin")
PUNK = CryptoAsset(AssetType.NFT, "cryptopunks", "PUNK", "CryptoPunks")


class _InlineWorker:
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


def _snapshot(asset: CryptoAsset = BTC) -> CryptoSnapshot:
    return CryptoSnapshot(
        asset=asset,
        vs_currency="usd",
        price=70_000.0,
        points=(
            CryptoPoint("2026-05-21T00:00:00+00:00", 68_000.0),
            CryptoPoint("2026-05-22T00:00:00+00:00", 70_000.0),
        ),
        fetched_at=dt.datetime(2026, 5, 22, tzinfo=dt.timezone.utc),
        change_pct_24h=2.5,
    )


class TestCryptoState:
    def test_prefs_round_trip_assets_and_samples(self):
        payload = prefs_payload(
            assets=(BTC, PUNK),
            active_index=1,
            chart_interval=ChartInterval.MONTH,
            vs_currency="USD",
            samples={
                "nft:cryptopunks": (CryptoPoint("2026-05-22T00:00:00+00:00", 42),)
            },
        )

        prefs = prefs_from_mapping(payload)

        assert prefs.assets == (BTC, PUNK)
        assert prefs.active_index == 1
        assert prefs.chart_interval == ChartInterval.MONTH
        assert prefs.vs_currency == "usd"
        assert prefs.samples["nft:cryptopunks"][0].price == 42

    def test_parse_coin_market_payload(self):
        asset = parse_coin_market_payload(
            data=[
                {
                    "id": "bitcoin",
                    "symbol": "btc",
                    "name": "Bitcoin",
                    "current_price": 70_000,
                }
            ],
            fallback=BTC,
        )

        assert asset == BTC

    def test_parse_market_chart_payload(self):
        points = parse_market_chart_payload(
            data={"prices": [[1_775_000_000_000, 10.5], [1_775_086_400_000, 11.0]]}
        )

        assert len(points) == 2
        assert points[0].price == 10.5
        assert points[0].timestamp.startswith("2026")

    def test_parse_nft_payload_reads_usd_floor(self):
        parsed = parse_nft_payload(
            data={
                "id": "cryptopunks",
                "symbol": "punk",
                "name": "CryptoPunks",
                "floor_price": {"usd": 95_000},
            },
            fallback=PUNK,
        )

        assert parsed == (PUNK, 95_000.0)

    def test_append_local_sample_retains_recent_points(self):
        now = dt.datetime(2026, 5, 22, tzinfo=dt.timezone.utc)
        samples = append_local_sample(samples={}, asset=PUNK, price=91_000, now=now)

        assert samples["nft:cryptopunks"] == (
            CryptoPoint("2026-05-22T00:00:00+00:00", 91_000.0),
        )

    def test_format_price_and_change(self):
        assert format_price(70_000, vs_currency="usd") == "$70,000"
        assert format_change(_snapshot().points) == "+2.94%"

    def test_tooltip_includes_price_interval_and_chart_change(self):
        text = build_tooltip(
            asset=BTC,
            snapshot=_snapshot(),
            loading=False,
            fetch_failed=False,
            chart_interval=ChartInterval.WEEK,
        )

        assert "Bitcoin (BTC)" in text
        assert "$70,000" in text
        assert "Interval: Week" in text
        assert "Change: +2.94%" in text
        assert "24h:" not in text


class TestCryptoFetch:
    def test_fetch_coin_snapshot_uses_coingecko_endpoints(self, monkeypatch):
        calls: list[tuple[str, dict[str, object]]] = []

        def fake_fetch(path, params):
            calls.append((path, dict(params)))
            if path == "/coins/markets":
                return [
                    {
                        "id": "bitcoin",
                        "symbol": "btc",
                        "name": "Bitcoin",
                        "current_price": 70_000,
                        "price_change_percentage_24h": 2.5,
                    }
                ]
            return {
                "prices": [[1_775_000_000_000, 68_000], [1_775_100_000_000, 70_000]]
            }

        monkeypatch.setattr(crypto_state_mod, "_fetch_json", fake_fetch)

        snapshot = crypto_state_mod.fetch_coin_snapshot(
            asset=BTC,
            chart_interval=ChartInterval.WEEK,
            vs_currency="usd",
        )

        assert snapshot is not None
        assert snapshot.price == 70_000
        assert len(snapshot.points) == 2
        assert calls == [
            (
                "/coins/markets",
                {
                    "vs_currency": "usd",
                    "ids": "bitcoin",
                    "price_change_percentage": "24h",
                },
            ),
            (
                "/coins/bitcoin/market_chart",
                {"vs_currency": "usd", "days": 7},
            ),
        ]


class TestCryptoApplet:
    def test_applet_presents_snapshot_tooltip(self, monkeypatch):
        monkeypatch.setattr(
            crypto_applet_mod,
            "render_icon",
            lambda **_kwargs: object(),
        )

        with patch("docking.applets.crypto.applet.BackgroundWorker", _InlineWorker):
            applet = CryptoApplet(icon_size=48)
            applet._snapshot = _snapshot()
            applet.present()

        assert "Bitcoin" in applet.item.name
        assert "$70,000" in applet.item.name

    def test_fetch_result_adds_nft_local_sample(self, monkeypatch):
        monkeypatch.setattr(
            crypto_applet_mod,
            "render_icon",
            lambda **_kwargs: object(),
        )
        with patch("docking.applets.crypto.applet.BackgroundWorker", _InlineWorker):
            applet = CryptoApplet(icon_size=48)
        nft_snapshot = CryptoSnapshot(
            asset=PUNK,
            vs_currency="usd",
            price=95_000,
            points=(),
            fetched_at=dt.datetime(2026, 5, 22, tzinfo=dt.timezone.utc),
        )
        applet._assets = [PUNK]
        applet._active_index = 0
        applet._fetch_request_id = 7

        applet._on_fetch_result(request_id=7, snapshot=nft_snapshot)

        assert applet._snapshot is not None
        assert applet._snapshot.points[-1].price == 95_000


class TestCryptoRender:
    def test_render_icon_returns_pixbuf(self):
        pixbuf = render_icon(
            size=48,
            snapshot=_snapshot(),
            asset_symbol="BTC",
            asset_type=AssetType.COIN,
            pulse_phase=0.4,
        )

        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48
