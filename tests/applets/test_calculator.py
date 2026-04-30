"""Tests for the Calculator applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.calculator.applet as calculator_applet_mod
from docking.applets.calculator.applet import CalculatorApplet
from docking.applets.calculator.state import evaluate, prefs_payload
from docking.core.config import Config


def _make_applet(config: Config | None = None) -> CalculatorApplet:
    return CalculatorApplet(48, config=config)


class _FakePopupWindow:
    def __init__(self) -> None:
        self.child = None
        self.moved_to = None
        self.destroyed = False

    def set_decorated(self, _value: bool) -> None:
        return

    def set_skip_taskbar_hint(self, _value: bool) -> None:
        return

    def set_type_hint(self, _hint) -> None:
        return

    def get_child(self):
        return self.child

    def remove(self, _child) -> None:
        self.child = None

    def add(self, child) -> None:
        self.child = child

    def show_all(self) -> None:
        return

    def get_preferred_size(self):
        return None, SimpleNamespace(width=80, height=40)

    def get_screen(self):
        return SimpleNamespace(get_width=lambda: 320, get_height=lambda: 240)

    def move(self, x: int, y: int) -> None:
        self.moved_to = (x, y)

    def destroy(self) -> None:
        self.destroyed = True


class _FakeEntry:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self._callbacks: dict[str, object] = {}
        self.cursor = None

    def set_text(self, text: str) -> None:
        self._text = text

    def get_text(self) -> str:
        return self._text

    def set_position(self, pos: int) -> None:
        self.cursor = pos

    def connect(self, signal: str, callback) -> None:
        self._callbacks[signal] = callback

    def emit(self, signal: str) -> None:
        callback = self._callbacks[signal]
        callback(self)


class _FakeBuildFontDescription:
    def __init__(self) -> None:
        self.family = ""

    def set_family(self, family: str) -> None:
        self.family = family


class _FakeBuildPangoContext:
    def __init__(self) -> None:
        self.font_description = _FakeBuildFontDescription()

    def get_font_description(self) -> _FakeBuildFontDescription:
        return self.font_description


class _FakeBuildEntry(_FakeEntry):
    def __init__(self) -> None:
        super().__init__()
        self.alignment = 0.0
        self.font = None
        self.context = _FakeBuildPangoContext()

    def set_alignment(self, value: float) -> None:
        self.alignment = value

    def get_pango_context(self) -> _FakeBuildPangoContext:
        return self.context

    def override_font(self, font_desc) -> None:
        self.font = font_desc


class _FakeBuildButton:
    def __init__(self, label: str = "") -> None:
        self.label = label
        self.connected = None

    def connect(self, signal: str, callback, *args) -> None:
        self.connected = (signal, callback, args)


class _FakeBuildBox:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.children: list[object] = []

    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)


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
    @staticmethod
    def _patch_popup(monkeypatch, applet: CalculatorApplet, popup: _FakePopupWindow):
        wrapped_children: list[object] = []

        def build_popup_content():
            applet._entry = _FakeEntry(applet._last_expr)
            applet._entry.connect("activate", lambda _entry: applet._do_evaluate())
            return {"expr": applet._last_expr}

        def show_wrapped_popup(*, window, content, gap_px):
            _ = gap_px
            window.add(content)
            window.show_all()
            window.move(80, 80)

        monkeypatch.setattr(calculator_applet_mod, "create_popup_window", lambda: popup)
        monkeypatch.setattr(
            calculator_applet_mod,
            "show_wrapped_popup",
            show_wrapped_popup,
        )
        monkeypatch.setattr(applet, "_build_popup_content", build_popup_content)
        return wrapped_children

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
        fake_popup = _FakePopupWindow()
        self._patch_popup(monkeypatch, applet, fake_popup)
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
        assert fake_popup.moved_to == (80, 80)

        applet.stop()

    def test_show_popup_uses_themed_surface_wrapper(self, monkeypatch):
        applet = _make_applet()
        fake_popup = _FakePopupWindow()
        show_wrapped_popup = MagicMock()
        monkeypatch.setattr(
            calculator_applet_mod, "create_popup_window", lambda: fake_popup
        )
        monkeypatch.setattr(
            calculator_applet_mod,
            "show_wrapped_popup",
            show_wrapped_popup,
        )
        monkeypatch.setattr(applet, "_build_popup_content", lambda: "content")

        applet._show_popup()

        show_wrapped_popup.assert_called_once_with(
            window=fake_popup,
            content="content",
            gap_px=calculator_applet_mod.POPUP_CURSOR_GAP_PX,
        )
        applet.stop()

    def test_activate_signal_evaluates_entry(self, monkeypatch):
        applet = _make_applet()
        self._patch_popup(monkeypatch, applet, _FakePopupWindow())
        applet._show_popup()
        assert applet._entry is not None
        applet._entry.set_text("2+3")

        applet._entry.emit("activate")

        assert applet._entry.get_text() == "5"
        assert applet._last_expr == "5"
        applet.stop()

    def test_build_popup_content_creates_entry_and_button_grid(self, monkeypatch):
        applet = _make_applet()
        applet._last_expr = "7*6"
        monkeypatch.setattr(
            calculator_applet_mod,
            "Gtk",
            SimpleNamespace(
                Box=_FakeBuildBox,
                Entry=_FakeBuildEntry,
                Button=_FakeBuildButton,
                Orientation=SimpleNamespace(VERTICAL=1, HORIZONTAL=2),
            ),
        )

        box = calculator_applet_mod.CalculatorApplet._build_popup_content(applet)

        assert isinstance(box, _FakeBuildBox)
        assert isinstance(applet._entry, _FakeBuildEntry)
        assert applet._entry.get_text() == "7*6"
        assert applet._entry.alignment == 1.0
        assert applet._entry.font.family == "monospace"
        assert len(box.children) == 1 + len(calculator_applet_mod.BUTTON_ROWS)
        first_row = box.children[1]
        assert isinstance(first_row, _FakeBuildBox)
        assert [button.label for button in first_row.children] == list(
            calculator_applet_mod.BUTTON_ROWS[0]
        )

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
