"""Tests for crypto state helper functions."""

from __future__ import annotations

from docking.applets.crypto.state import (
    DEFAULT_VS_CURRENCY,
    AssetType,
    ChartInterval,
    CryptoAsset,
    asset_key,
    normalize_asset,
    normalize_asset_id,
    normalize_asset_type,
    normalize_assets,
    normalize_chart_interval,
    normalize_name,
    normalize_symbol,
    normalize_vs_currency,
    prefs_from_mapping,
    prefs_payload,
)


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
