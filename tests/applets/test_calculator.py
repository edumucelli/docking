"""Tests for the Calculator applet."""

from __future__ import annotations

from unittest.mock import MagicMock

import docking.applets.calculator.applet as calculator_applet_mod
from docking.applets.calculator.applet import CalculatorApplet
from docking.applets.calculator.state import evaluate, prefs_payload
from docking.core.config import Config
from docking.ui.display import ScreenPosition


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

    def test_show_popup_builds_content_and_reuses_window(self, monkeypatch):
        applet = _make_applet()
        monkeypatch.setattr(
            calculator_applet_mod,
            "get_pointer_position",
            lambda _display: ScreenPosition(x=120, y=140),
        )

        applet._show_popup()
        first_popup = applet._popup
        first_child = first_popup.get_child()
        assert applet._entry is not None
        assert applet._entry.get_text() == ""

        applet._last_expr = "5+5"
        applet._show_popup()

        assert applet._popup is first_popup
        assert applet._popup.get_child() is not first_child
        assert applet._entry is not None
        assert applet._entry.get_text() == "5+5"

        applet.stop()

    def test_show_popup_uses_themed_surface_wrapper(self, monkeypatch):
        applet = _make_applet()
        monkeypatch.setattr(
            calculator_applet_mod,
            "get_pointer_position",
            lambda _display: ScreenPosition(x=120, y=140),
        )

        applet._show_popup()

        popup_child = applet._popup.get_child()
        assert isinstance(popup_child, calculator_applet_mod.Gtk.Frame)
        assert "applet-popup-surface" in popup_child.get_style_context().list_classes()
        applet.stop()

    def test_activate_signal_evaluates_entry(self, monkeypatch):
        applet = _make_applet()
        monkeypatch.setattr(
            calculator_applet_mod,
            "get_pointer_position",
            lambda _display: ScreenPosition(x=100, y=120),
        )
        applet._show_popup()
        assert applet._entry is not None
        applet._entry.set_text("2+3")

        applet._entry.emit("activate")

        assert applet._entry.get_text() == "5"
        assert applet._last_expr == "5"
        applet.stop()

    def test_button_clear_backspace_insert_and_equals(self):
        applet = _make_applet()
        applet._entry = MagicMock()
        applet._entry.get_text.return_value = "123"
        applet._entry.get_position.return_value = 1
        applet._do_evaluate = MagicMock()

        applet._on_button(MagicMock(), "C")
        applet._entry.set_text.assert_any_call("")

        applet._on_button(MagicMock(), "←")
        applet._entry.set_text.assert_any_call("12")

        applet._on_button(MagicMock(), "=")
        applet._do_evaluate.assert_called_once_with()

        applet._on_button(MagicMock(), "7")
        applet._entry.insert_text.assert_called_once_with("7", 1)
        applet._entry.set_position.assert_called_with(2)

    def test_button_handler_is_noop_without_entry(self):
        applet = _make_applet()
        applet._entry = None

        applet._on_button(MagicMock(), "7")

    def test_do_evaluate_is_noop_without_entry(self):
        applet = _make_applet()
        applet._entry = None

        applet._do_evaluate()

    def test_do_evaluate_updates_entry_and_saves_prefs(self, tmp_path):
        path = tmp_path / "dock.json"
        config = Config()
        config.save(path)
        config = Config.load(path)
        applet = _make_applet(config=config)
        applet._entry = MagicMock()
        applet._entry.get_text.return_value = "8/2"

        applet._do_evaluate()

        applet._entry.set_text.assert_called_once_with("4")
        applet._entry.set_position.assert_called_once_with(-1)
        assert applet._last_expr == "4"
        reloaded = Config.load(path)
        assert reloaded.applet_prefs["calculator"]["last_expression"] == "4"
