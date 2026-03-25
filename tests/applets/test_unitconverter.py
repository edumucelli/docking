"""Tests for the Unit Converter applet."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import docking.applets.unitconverter.state as uc_state
from docking.applets.unitconverter import UnitConverterApplet
from docking.applets.unitconverter.state import (
    Category,
    Unit,
    convert,
    currency_available,
    format_result,
    get_categories,
    get_units,
    prefs_payload,
    set_currency_units,
)
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


def _make_applet(config: Config | None = None) -> UnitConverterApplet:
    with patch(
        "docking.applets.unitconverter.applet.BackgroundWorker", _ImmediateWorker
    ):
        return UnitConverterApplet(48, config=config)


def _unit(cat: Category, symbol: str) -> Unit:
    for u in get_units(cat):
        if u.symbol == symbol:
            return u
    raise ValueError(f"No unit {symbol} in {cat}")


# -- Conversion tests ---------------------------------------------------------


class TestConvert:
    def test_km_to_mi(self):
        result = convert(
            value=1.0,
            from_unit=_unit(Category.LENGTH, "km"),
            to_unit=_unit(Category.LENGTH, "mi"),
            category=Category.LENGTH,
        )
        assert abs(result - 0.621371) < 0.001

    def test_mi_to_km(self):
        result = convert(
            value=1.0,
            from_unit=_unit(Category.LENGTH, "mi"),
            to_unit=_unit(Category.LENGTH, "km"),
            category=Category.LENGTH,
        )
        assert abs(result - 1.60934) < 0.001

    def test_kg_to_lb(self):
        result = convert(
            value=1.0,
            from_unit=_unit(Category.WEIGHT, "kg"),
            to_unit=_unit(Category.WEIGHT, "lb"),
            category=Category.WEIGHT,
        )
        assert abs(result - 2.20462) < 0.001

    def test_celsius_to_fahrenheit(self):
        result = convert(
            value=100.0,
            from_unit=_unit(Category.TEMPERATURE, "C"),
            to_unit=_unit(Category.TEMPERATURE, "F"),
            category=Category.TEMPERATURE,
        )
        assert abs(result - 212.0) < 0.01

    def test_fahrenheit_to_celsius(self):
        result = convert(
            value=32.0,
            from_unit=_unit(Category.TEMPERATURE, "F"),
            to_unit=_unit(Category.TEMPERATURE, "C"),
            category=Category.TEMPERATURE,
        )
        assert abs(result - 0.0) < 0.01

    def test_celsius_to_kelvin(self):
        result = convert(
            value=0.0,
            from_unit=_unit(Category.TEMPERATURE, "C"),
            to_unit=_unit(Category.TEMPERATURE, "K"),
            category=Category.TEMPERATURE,
        )
        assert abs(result - 273.15) < 0.01

    def test_kelvin_to_fahrenheit(self):
        result = convert(
            value=273.15,
            from_unit=_unit(Category.TEMPERATURE, "K"),
            to_unit=_unit(Category.TEMPERATURE, "F"),
            category=Category.TEMPERATURE,
        )
        assert abs(result - 32.0) < 0.01

    def test_liter_to_gallon(self):
        result = convert(
            value=1.0,
            from_unit=_unit(Category.VOLUME, "l"),
            to_unit=_unit(Category.VOLUME, "gal"),
            category=Category.VOLUME,
        )
        assert abs(result - 0.264172) < 0.001

    def test_mph_to_kmh(self):
        result = convert(
            value=60.0,
            from_unit=_unit(Category.SPEED, "mph"),
            to_unit=_unit(Category.SPEED, "km/h"),
            category=Category.SPEED,
        )
        assert abs(result - 96.5606) < 0.01

    def test_hour_to_seconds(self):
        result = convert(
            value=1.0,
            from_unit=_unit(Category.TIME, "h"),
            to_unit=_unit(Category.TIME, "s"),
            category=Category.TIME,
        )
        assert result == 3600.0

    def test_gb_to_mb(self):
        result = convert(
            value=1.0,
            from_unit=_unit(Category.DATA, "GB"),
            to_unit=_unit(Category.DATA, "MB"),
            category=Category.DATA,
        )
        assert result == 1024.0

    def test_same_unit(self):
        u = _unit(Category.LENGTH, "m")
        result = convert(value=42.0, from_unit=u, to_unit=u, category=Category.LENGTH)
        assert result == 42.0

    def test_currency_eur_to_usd(self):
        # Factor = 1/rate. Rate 1.08 means 1 EUR = 1.08 USD.
        eur = Unit("Euro", "EUR", 1.0)
        usd = Unit("US Dollar", "USD", 1.0 / 1.08)
        result = convert(
            value=100.0, from_unit=eur, to_unit=usd, category=Category.CURRENCY
        )
        assert abs(result - 108.0) < 0.01

    def test_currency_usd_to_eur(self):
        eur = Unit("Euro", "EUR", 1.0)
        usd = Unit("US Dollar", "USD", 1.0 / 1.08)
        result = convert(
            value=108.0, from_unit=usd, to_unit=eur, category=Category.CURRENCY
        )
        assert abs(result - 100.0) < 0.01

    def test_currency_cross_rate(self):
        # USD rate 1.08, GBP rate 0.85 -> 1 USD in GBP = (1/1.08) / (1/0.85)
        usd = Unit("US Dollar", "USD", 1.0 / 1.08)
        gbp = Unit("British Pound", "GBP", 1.0 / 0.85)
        result = convert(
            value=1.0, from_unit=usd, to_unit=gbp, category=Category.CURRENCY
        )
        expected = 0.85 / 1.08
        assert abs(result - expected) < 0.01


class TestFormatResult:
    def test_large_number(self):
        assert format_result(1234.56) == "1,234.56"

    def test_medium_number(self):
        assert format_result(3.14159) == "3.1416"

    def test_integer_like(self):
        assert format_result(5.0) == "5"

    def test_small_number(self):
        result = format_result(0.001)
        assert "0.001" in result

    def test_very_small(self):
        result = format_result(0.0000001)
        assert "1" in result


class TestPrefsPayload:
    def test_round_trip(self):
        payload = prefs_payload(category_index=2, from_index=1, to_index=3)
        assert payload["category_index"] == 2
        assert payload["from_index"] == 1
        assert payload["to_index"] == 3


# -- Currency state tests -----------------------------------------------------


class TestCurrencyState:
    def setup_method(self):
        set_currency_units(())

    def teardown_method(self):
        set_currency_units(())

    def test_no_currency_by_default(self):
        assert not currency_available()
        assert Category.CURRENCY not in get_categories()

    def test_currency_appears_after_set(self):
        units = (Unit("Euro", "EUR", 1.0), Unit("US Dollar", "USD", 1.08))
        set_currency_units(units)
        assert currency_available()
        assert Category.CURRENCY in get_categories()
        assert get_units(Category.CURRENCY) == units

    def test_static_categories_always_present(self):
        cats = get_categories()
        assert Category.LENGTH in cats
        assert Category.WEIGHT in cats
        assert Category.TEMPERATURE in cats

    def test_fetch_currency_rates_returns_none_on_failure(self, monkeypatch):
        monkeypatch.setattr(
            uc_state.urllib.request,
            "urlopen",
            MagicMock(side_effect=OSError("no network")),
        )
        result = uc_state.fetch_currency_rates()
        assert result is None

    def test_fetch_currency_rates_parses_v2_list(self, monkeypatch):
        import json

        fake_data = json.dumps(
            [
                {"base": "EUR", "quote": "USD", "rate": 1.08},
                {"base": "EUR", "quote": "GBP", "rate": 0.85},
            ]
        ).encode()

        class FakeResp:
            def read(self):
                return fake_data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(
            uc_state.urllib.request, "urlopen", lambda *a, **kw: FakeResp()
        )
        result = uc_state.fetch_currency_rates()
        assert result is not None
        symbols = [u.symbol for u in result]
        assert "EUR" in symbols
        assert "USD" in symbols
        assert "GBP" in symbols

    def test_fetch_currency_rates_parses_dict_format(self, monkeypatch):
        import json

        fake_data = json.dumps({"rates": {"USD": 1.08, "JPY": 163.5}}).encode()

        class FakeResp:
            def read(self):
                return fake_data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        monkeypatch.setattr(
            uc_state.urllib.request, "urlopen", lambda *a, **kw: FakeResp()
        )
        result = uc_state.fetch_currency_rates()
        assert result is not None
        symbols = [u.symbol for u in result]
        assert "EUR" in symbols
        assert "USD" in symbols


# -- Applet tests -------------------------------------------------------------


class TestAppletCreation:
    def test_creates_with_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_tooltip(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "Unit Converter" in applet.item.name

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            with patch(
                "docking.applets.unitconverter.applet.BackgroundWorker",
                _ImmediateWorker,
            ):
                applet = UnitConverterApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None


class TestAppletPrefs:
    def test_loads_prefs_from_config(self):
        config = Config(
            applet_prefs={
                "unitconverter": {
                    "category_index": 3,
                    "from_index": 1,
                    "to_index": 2,
                }
            }
        )
        applet = _make_applet(config=config)
        assert applet._cat_idx == 3
        assert applet._from_idx == 1
        assert applet._to_idx == 2

    def test_clamps_invalid_category(self):
        config = Config(applet_prefs={"unitconverter": {"category_index": 999}})
        applet = _make_applet(config=config)
        cats = get_categories()
        assert applet._cat_idx == len(cats) - 1

    def test_saves_prefs(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = _make_applet(config=config)
        applet._cat_idx = 2
        applet._from_idx = 1
        applet._to_idx = 3
        applet._save_prefs()
        reloaded = Config.load(path)
        prefs = reloaded.applet_prefs["unitconverter"]
        assert prefs["category_index"] == 2
        assert prefs["from_index"] == 1


class TestAppletPopup:
    def test_on_clicked_toggles_popup(self, monkeypatch):
        applet = _make_applet()
        show = MagicMock()
        monkeypatch.setattr(applet, "_show_popup", show)
        applet.on_clicked()
        show.assert_called_once()

    def test_on_clicked_hides_visible_popup(self):
        applet = _make_applet()
        popup = MagicMock()
        popup.get_visible.return_value = True
        applet._popup = popup
        applet.on_clicked()
        popup.hide.assert_called_once()

    def test_stop_destroys_popup(self):
        applet = _make_applet()
        popup = MagicMock()
        applet._popup = popup
        applet.stop()
        popup.destroy.assert_called_once()
        assert applet._popup is None


class TestCategories:
    def test_all_static_categories_have_units(self):
        for cat in get_categories():
            if cat == Category.CURRENCY:
                continue
            assert len(get_units(cat)) >= 2

    def test_all_static_categories_have_unique_symbols(self):
        for cat in get_categories():
            if cat == Category.CURRENCY:
                continue
            symbols = [u.symbol for u in get_units(cat)]
            assert len(symbols) == len(set(symbols))
