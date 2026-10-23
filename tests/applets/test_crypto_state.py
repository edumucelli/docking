"""Tests for crypto state helper functions."""

from __future__ import annotations

import datetime as dt

import pytest

from docking.applets.crypto import state as crypto_state
from docking.applets.crypto.state import (
    DEFAULT_VS_CURRENCY,
    AssetType,
    ChartInterval,
    CryptoAsset,
    CryptoPoint,
    append_local_sample,
    asset_key,
    fetch_crypto_snapshot,
    normalize_asset,
    normalize_asset_id,
    normalize_asset_type,
    normalize_assets,
    normalize_chart_interval,
    normalize_name,
    normalize_symbol,
    normalize_vs_currency,
    parse_coin_market_payload,
    parse_market_chart_payload,
    prefs_from_mapping,
    prefs_payload,
    samples_from_pref_value,
    samples_payload,
)

BITCOIN = CryptoAsset(AssetType.COIN, "bitcoin", "BTC", "Bitcoin")


class TestNormalizeAssetType:
    def test_returns_existing_asset_type(self):
        assert normalize_asset_type(AssetType.COIN) == AssetType.COIN
        assert normalize_asset_type(AssetType.NFT) == AssetType.NFT

    def test_parses_string(self):
        assert normalize_asset_type("coin") == AssetType.COIN
        assert normalize_asset_type("NFT") == AssetType.NFT
        assert normalize_asset_type("  COIN  ") == AssetType.COIN

    def test_invalid_falls_back_to_coin(self):
        assert normalize_asset_type("invalid") == AssetType.COIN
        assert normalize_asset_type("") == AssetType.COIN
        assert normalize_asset_type(None) == AssetType.COIN
        assert normalize_asset_type(42) == AssetType.COIN


class TestNormalizeAssetId:
    def test_returns_cleaned_id(self):
        assert normalize_asset_id("  Bitcoin  ") == "bitcoin"

    def test_empty_falls_back(self):
        assert normalize_asset_id("") == "bitcoin"
        assert normalize_asset_id(None) == "bitcoin"

    def test_custom_fallback(self):
        assert normalize_asset_id("", fallback="ethereum") == "ethereum"


class TestNormalizeSymbol:
    def test_uppercases_and_strips(self):
        assert normalize_symbol(" btc ", fallback="x") == "BTC"

    def test_empty_falls_back_uppercased(self):
        assert normalize_symbol("", fallback="btc") == "BTC"
        assert normalize_symbol(None, fallback="eth") == "ETH"


class TestNormalizeName:
    def test_strips_whitespace(self):
        assert normalize_name("  Bitcoin  ", fallback="x") == "Bitcoin"

    def test_empty_falls_back(self):
        assert normalize_name("", fallback="Bitcoin") == "Bitcoin"
        assert normalize_name(None, fallback="Ethereum") == "Ethereum"


class TestNormalizeVsCurrency:
    def test_valid_three_letter_code(self):
        assert normalize_vs_currency("usd") == "usd"
        assert normalize_vs_currency(" EUR ") == "eur"

    def test_invalid_falls_back(self):
        assert normalize_vs_currency("") == DEFAULT_VS_CURRENCY
        assert normalize_vs_currency("us") == DEFAULT_VS_CURRENCY
        assert normalize_vs_currency("usdt") == DEFAULT_VS_CURRENCY
        assert normalize_vs_currency(None) == DEFAULT_VS_CURRENCY


class TestNormalizeChartInterval:
    def test_returns_existing_interval(self):
        assert normalize_chart_interval(ChartInterval.DAY) == ChartInterval.DAY

    def test_parses_string(self):
        assert normalize_chart_interval("day") == ChartInterval.DAY
        assert normalize_chart_interval("WEEK") == ChartInterval.WEEK
        assert normalize_chart_interval("month") == ChartInterval.MONTH

    def test_invalid_falls_back_to_day(self):
        assert normalize_chart_interval("invalid") == ChartInterval.DAY
        assert normalize_chart_interval("") == ChartInterval.DAY
        assert normalize_chart_interval(None) == ChartInterval.DAY


class TestAssetKey:
    def test_combines_type_and_id(self):
        asset = CryptoAsset(
            asset_type=AssetType.COIN,
            asset_id="bitcoin",
            symbol="BTC",
            name="Bitcoin",
        )
        assert asset_key(asset) == "coin:bitcoin"


class TestNormalizeAsset:
    def test_cleans_all_fields(self):
        asset = normalize_asset(
            asset_type="coin",
            asset_id="  bitcoin ",
            symbol=" btc ",
            name=" Bitcoin ",
        )
        assert asset.asset_type == AssetType.COIN
        assert asset.asset_id == "bitcoin"
        assert asset.symbol == "BTC"
        assert asset.name == "Bitcoin"

    def test_empty_symbol_uses_fallback(self):
        asset = normalize_asset(
            asset_type=AssetType.COIN,
            asset_id="bitcoin",
            symbol="",
            name="Bitcoin",
        )
        assert asset.symbol == "BITC"  # first 4 chars of asset_id, uppercase


class TestNormalizeAssets:
    def test_removes_duplicates(self):
        a1 = CryptoAsset(AssetType.COIN, "bitcoin", "BTC", "Bitcoin")
        a2 = CryptoAsset(AssetType.COIN, "bitcoin", "btc", "Bitcoin")
        a3 = CryptoAsset(AssetType.COIN, "ethereum", "ETH", "Ethereum")

        result = normalize_assets([a1, a2, a3])
        assert len(result) == 2

    def test_empty_sequence_returns_defaults(self):
        result = normalize_assets([])
        assert len(result) == 3  # defaults: BTC, ETH, SOL


class TestPrefsRoundTrip:
    def test_prefs_from_none_returns_defaults(self):
        prefs = prefs_from_mapping(None)
        assert prefs.vs_currency == DEFAULT_VS_CURRENCY
        assert prefs.chart_interval == ChartInterval.DAY
        assert len(prefs.assets) == 3  # default assets: BTC, ETH, SOL

    def test_prefs_from_partial_mapping(self):
        prefs = prefs_from_mapping({"vs_currency": "eur"})
        assert prefs.vs_currency == "eur"
        assert prefs.chart_interval == ChartInterval.DAY

    def test_prefs_from_invalid_currency_falls_back(self):
        prefs = prefs_from_mapping({"vs_currency": 123})
        assert prefs.vs_currency == DEFAULT_VS_CURRENCY

    def test_prefs_payload_includes_all_keys(self):
        payload = prefs_payload(
            assets=(),
            active_index=0,
            chart_interval=ChartInterval.DAY,
            vs_currency="usd",
        )
        assert "assets" in payload
        assert "active_index" in payload
        assert "chart_interval" in payload
        assert "vs_currency" in payload
        assert payload["vs_currency"] == "usd"

    def test_prefs_payload_normalizes_interval(self):
        payload = prefs_payload(
            assets=(),
            active_index=0,
            chart_interval="week",
            vs_currency="eur",
        )
        assert payload["chart_interval"] == "week"
        assert payload["vs_currency"] == "eur"


class TestPrefsFromMappingRobustness:
    """Persisted preferences come from disk and may be hand-edited or stale."""

    def test_unparseable_active_index_falls_back_to_zero(self):
        for raw in ("not-a-number", None, [], {}):
            prefs = prefs_from_mapping({"active_index": raw})

            assert prefs.active_index == 0, raw

    def test_non_mapping_asset_entries_are_skipped(self):
        prefs = prefs_from_mapping(
            {
                "assets": [
                    "bitcoin",
                    None,
                    {
                        "type": "coin",
                        "id": "ethereum",
                        "symbol": "ETH",
                        "name": "Ethereum",
                    },
                ]
            }
        )

        assert [asset.asset_id for asset in prefs.assets] == ["ethereum"]

    def test_assets_of_the_wrong_shape_fall_back_to_defaults(self):
        for raw in ("bitcoin", 7, None):
            prefs = prefs_from_mapping({"assets": raw})

            assert prefs.assets, raw
            assert prefs.assets[0].asset_id == "bitcoin"


class TestSamplesFromPrefValue:
    def test_non_mapping_returns_empty(self):
        assert samples_from_pref_value(None) == {}
        assert samples_from_pref_value(["bitcoin"]) == {}
        assert samples_from_pref_value("bitcoin") == {}

    def test_malformed_keys_values_and_points_are_dropped(self):
        good = CryptoPoint("2024-01-01T00:00:00+00:00", 3.5)
        parsed = samples_from_pref_value(
            {
                1: [{"timestamp": good.timestamp, "price": 1.0}],  # non-string key
                "coin:bitcoin": "not-a-sequence",  # string is not a point list
                "coin:ethereum": [
                    "not-a-mapping",
                    {"price": 2.0},  # no timestamp
                    {"timestamp": good.timestamp, "price": "nope"},  # unusable price
                    {"timestamp": good.timestamp, "price": 0.0},  # non-positive
                    {"timestamp": good.timestamp, "price": 3.5},
                ],
            }
        )

        assert list(parsed) == ["coin:ethereum"]
        assert parsed["coin:ethereum"] == (good,)


class TestSamplesPayload:
    def test_assets_without_points_are_omitted(self):
        payload = samples_payload(
            {
                "coin:bitcoin": (),
                "coin:ethereum": (CryptoPoint("2024-01-01T00:00:00+00:00", 1.0),),
            }
        )

        assert list(payload) == ["coin:ethereum"]


class TestAppendLocalSample:
    def test_non_positive_price_leaves_samples_untouched(self):
        samples = {"coin:bitcoin": (CryptoPoint("2024-01-01T00:00:00+00:00", 5.0),)}

        for price in (0.0, -1.0):
            updated = append_local_sample(samples=samples, asset=BITCOIN, price=price)

            assert updated == samples
            assert updated is not samples

    def test_a_repeated_timestamp_replaces_the_last_point(self):
        """Two refreshes inside the same second update one point, not two."""
        now = dt.datetime(2024, 5, 1, 12, 0, tzinfo=dt.timezone.utc)

        first = append_local_sample(samples={}, asset=BITCOIN, price=10.0, now=now)
        second = append_local_sample(samples=first, asset=BITCOIN, price=11.0, now=now)

        points = second[asset_key(BITCOIN)]
        assert len(points) == 1
        assert points[0].price == 11.0

    def test_a_later_timestamp_appends_a_point(self):
        earlier = dt.datetime(2024, 5, 1, 12, 0, tzinfo=dt.timezone.utc)
        later = earlier + dt.timedelta(seconds=1)

        first = append_local_sample(samples={}, asset=BITCOIN, price=10.0, now=earlier)
        second = append_local_sample(
            samples=first, asset=BITCOIN, price=11.0, now=later
        )

        points = second[asset_key(BITCOIN)]
        assert [point.price for point in points] == [10.0, 11.0]


class TestFetchCryptoSnapshotDispatch:
    def test_nft_assets_use_the_nft_endpoint(self, monkeypatch):
        calls: list[str] = []

        def fake_fetch_json(path, params):
            calls.append(path)
            return {"floor_price": {"usd": 12.5}}

        monkeypatch.setattr(crypto_state, "_fetch_json", fake_fetch_json)
        nft = CryptoAsset(AssetType.NFT, "cool-cats", "CAT", "Cool Cats")

        snapshot = fetch_crypto_snapshot(asset=nft)

        assert calls == ["/nfts/cool-cats"]
        assert snapshot is not None
        assert snapshot.asset.asset_type is AssetType.NFT
        assert snapshot.price == 12.5
        assert snapshot.points == ()

    def test_coin_snapshot_parses_market_and_chart(self, monkeypatch):
        def fake_fetch_json(path, params):
            if path == "/coins/markets":
                return [
                    {
                        "id": "bitcoin",
                        "symbol": "btc",
                        "name": "Bitcoin",
                        "current_price": 42000.0,
                        "price_change_percentage_24h": 1.5,
                    }
                ]
            return {"prices": [[1700000000000, 41000.0], [1700003600000, 42000.0]]}

        monkeypatch.setattr(crypto_state, "_fetch_json", fake_fetch_json)

        snapshot = fetch_crypto_snapshot(asset=BITCOIN)

        assert snapshot is not None
        assert snapshot.price == 42000.0
        assert snapshot.change_pct_24h == 1.5
        assert [point.price for point in snapshot.points] == [41000.0, 42000.0]

    def test_coin_snapshot_is_dropped_without_a_current_price(self, monkeypatch):
        def fake_fetch_json(path, params):
            return [{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin"}]

        monkeypatch.setattr(crypto_state, "_fetch_json", fake_fetch_json)

        assert fetch_crypto_snapshot(asset=BITCOIN) is None


class TestParseCoinMarketPayload:
    @pytest.mark.parametrize(
        "data",
        [None, "text", 5, [], {}, {"id": "bitcoin"}, [1, 2], ["text"]],
    )
    def test_malformed_payloads_return_none(self, data):
        assert parse_coin_market_payload(data=data, fallback=BITCOIN) is None

    def test_present_row_without_a_price_returns_none(self):
        payload = [{"id": "bitcoin", "current_price": None}]

        assert parse_coin_market_payload(data=payload, fallback=BITCOIN) is None

    def test_missing_fields_fall_back_to_the_requested_asset(self):
        payload = [{"current_price": 10.0}]

        parsed = parse_coin_market_payload(data=payload, fallback=BITCOIN)

        assert parsed == BITCOIN

    def test_reported_identity_overrides_the_fallback(self):
        payload = [
            {
                "id": "wrapped-bitcoin",
                "symbol": "WBTC",
                "name": "Wrapped Bitcoin",
                "current_price": 42000.0,
            }
        ]

        parsed = parse_coin_market_payload(data=payload, fallback=BITCOIN)

        assert parsed is not None
        assert parsed.asset_id == "wrapped-bitcoin"
        assert parsed.symbol == "WBTC"


class TestParseMarketChartPayload:
    @pytest.mark.parametrize("data", [None, "text", 5, [], [1, 2]])
    def test_malformed_payloads_return_empty(self, data):
        assert parse_market_chart_payload(data=data) == ()

    @pytest.mark.parametrize("prices", [None, "nope", 5, {}])
    def test_prices_must_be_a_non_string_sequence(self, prices):
        assert parse_market_chart_payload(data={"prices": prices}) == ()

    def test_malformed_and_non_positive_rows_are_skipped(self):
        payload = {
            "prices": [
                [1700000000000],  # too few columns
                "row",  # not a sequence row
                [None, 5.0],  # unusable timestamp
                [1700000000000, "nope"],  # unusable price
                [1700000000000, 0.0],  # non-positive
                [1700000000000, -1.0],  # non-positive
                [1700000000000, 41000.0],  # usable
            ]
        }

        points = parse_market_chart_payload(data=payload)

        assert len(points) == 1
        assert points[0].price == 41000.0
        assert (
            points[0].timestamp
            == dt.datetime.fromtimestamp(1700000000, dt.timezone.utc).isoformat()
        )
