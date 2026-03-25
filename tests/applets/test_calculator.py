"""Tests for the Calculator applet."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.applets.calculator import CalculatorApplet
from docking.applets.calculator.state import evaluate, prefs_payload
from docking.core.config import Config


def _make_applet(config: Config | None = None) -> CalculatorApplet:
    return CalculatorApplet(48, config=config)


# -- Evaluation tests ----------------------------------------------------------


class TestEvaluate:
    def test_addition(self):
        assert evaluate("2+2") == "4"

    def test_subtraction(self):
        assert evaluate("10-3") == "7"

    def test_multiplication(self):
        assert evaluate("6*7") == "42"

    def test_division(self):
        assert evaluate("10/4") == "2.5"

    def test_order_of_operations(self):
        assert evaluate("2+3*4") == "14"

    def test_parentheses(self):
        assert evaluate("(2+3)*4") == "20"

    def test_nested_parentheses(self):
        assert evaluate("((1+2)*(3+4))") == "21"

    def test_negative_number(self):
        assert evaluate("-5+3") == "-2"

    def test_decimal(self):
        assert evaluate("1.5+2.5") == "4"

    def test_decimal_result(self):
        assert evaluate("10/3") == "3.333333333"

    def test_division_by_zero(self):
        assert "zero" in evaluate("1/0").lower()

    def test_invalid_expression(self):
        assert "Error" in evaluate("abc")

    def test_empty_string(self):
        assert evaluate("") == ""

    def test_whitespace(self):
        assert evaluate("  2 + 2  ") == "4"

    def test_rejects_function_calls(self):
        assert "Error" in evaluate("__import__('os')")

    def test_rejects_power(self):
        assert "Error" in evaluate("2**10")

    def test_large_number(self):
        assert evaluate("999999*999999") == "999998000001"


class TestPrefsPayload:
    def test_round_trip(self):
        payload = prefs_payload(last_expression="42")
        assert payload["last_expression"] == "42"


# -- Applet tests --------------------------------------------------------------


class TestAppletCreation:
    def test_creates_with_icon(self):
        applet = _make_applet()
        assert applet.item.icon is not None

    def test_tooltip(self):
        applet = _make_applet()
        applet.refresh_tooltip()
        assert "Calculator" in applet.item.name

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = CalculatorApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None


class TestAppletPrefs:
    def test_loads_prefs(self):
        config = Config(applet_prefs={"calculator": {"last_expression": "42"}})
        applet = _make_applet(config=config)
        assert applet._last_expr == "42"

    def test_defaults_without_config(self):
        applet = _make_applet()
        assert applet._last_expr == ""

    def test_saves_prefs(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = _make_applet(config=config)
        applet._last_expr = "123"
        applet._save_prefs()
        reloaded = Config.load(path)
        assert reloaded.applet_prefs["calculator"]["last_expression"] == "123"


class TestAppletPopup:
    def test_on_clicked_shows_popup(self, monkeypatch):
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

    def test_stop_without_popup(self):
        applet = _make_applet()
        applet.stop()  # should not raise
