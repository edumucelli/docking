"""Tests for the Unit Converter applet."""

from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docking.applets.popup as applet_popup_mod
import docking.applets.unitconverter.applet as unitconverter_applet_mod
import docking.applets.unitconverter.state as uc_state
from docking.applets.unitconverter.applet import UnitConverterApplet
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


class _FakePopupWindow:
    def __init__(self) -> None:
        self.child = None
        self.destroyed = False
        self.visible = False
        self.presented = False
        self.connected = None
        self.size = None
        self.position = None
        self.resizable = None
        self.content = _FakeDialogContent()

    def connect(self, signal: str, callback) -> None:
        self.connected = (signal, callback)

    def set_skip_taskbar_hint(self, value: bool) -> None:
        self.skip_taskbar = value

    def set_skip_pager_hint(self, value: bool) -> None:
        self.skip_pager = value

    def set_default_size(self, width: int, height: int) -> None:
        self.size = (width, height)

    def set_position(self, position) -> None:
        self.position = position

    def set_resizable(self, value: bool) -> None:
        self.resizable = value

    def get_child(self):
        return self.child

    def remove(self, _child) -> None:
        self.child = None

    def add(self, child) -> None:
        self.child = child

    def show_all(self) -> None:
        self.visible = True

    def present(self) -> None:
        self.presented = True

    def hide(self) -> None:
        self.visible = False

    def get_visible(self) -> bool:
        return self.visible

    def get_content_area(self):
        return self.content

    def get_preferred_size(self):
        return None, SimpleNamespace(width=100, height=60)

    def get_screen(self):
        return SimpleNamespace(get_width=lambda: 320, get_height=lambda: 240)

    def move(self, x: int, y: int) -> None:
        self.moved_to = (x, y)

    def destroy(self) -> None:
        self.destroyed = True


class _FakeDialogContent:
    def __init__(self) -> None:
        self.children: list[object] = []
        self.spacing = None
        self.margins: list[tuple[str, int]] = []

    def set_spacing(self, value: int) -> None:
        self.spacing = value

    def set_margin_start(self, value: int) -> None:
        self.margins.append(("start", value))

    def set_margin_end(self, value: int) -> None:
        self.margins.append(("end", value))

    def set_margin_top(self, value: int) -> None:
        self.margins.append(("top", value))

    def set_margin_bottom(self, value: int) -> None:
        self.margins.append(("bottom", value))

    def get_children(self):
        return list(self.children)

    def remove(self, child) -> None:
        self.children.remove(child)

    def add(self, child) -> None:
        self.children.append(child)


class _FakeComboBoxText:
    def __init__(self) -> None:
        self.items: list[str] = []
        self.active = -1
        self.entry = _FakeEntry()
        self.entry_text_column = None

    @classmethod
    def new_with_entry(cls):
        return cls()

    def append_text(self, text: str) -> None:
        self.items.append(text)

    def remove_all(self) -> None:
        self.items.clear()
        self.active = -1
        self.entry.set_text("")

    def set_active(self, idx: int) -> None:
        self.active = idx
        if 0 <= idx < len(self.items):
            self.entry.set_text(self.items[idx])
        elif idx < 0:
            self.entry.set_text("")

    def get_active(self) -> int:
        return self.active

    def get_child(self) -> _FakeEntry:
        return self.entry

    def get_model(self) -> _FakeComboModel:
        return _FakeComboModel(self.items)

    def set_entry_text_column(self, column: int) -> None:
        self.entry_text_column = column

    def set_hexpand(self, _value: bool) -> None:
        return

    def connect(self, *_args) -> None:
        return


class _FakeEntry:
    def __init__(self, text: str = "") -> None:
        self.text = text
        self.placeholder = ""
        self._callbacks: dict[str, object] = {}
        self.selected_region = None

    def set_text(self, text: str) -> None:
        self.text = text

    def get_text(self) -> str:
        return self.text

    def set_placeholder_text(self, _text: str) -> None:
        self.placeholder = _text

    def connect(self, signal: str, callback) -> None:
        self._callbacks[signal] = callback

    def set_completion(self, completion) -> None:
        self.completion = completion

    def select_region(self, start: int, end: int) -> None:
        self.selected_region = (start, end)


class _FakeComboModel:
    def __init__(self, items: list[str]) -> None:
        self.items = items

    def get_value(self, tree_iter: int, _column: int) -> str:
        return self.items[tree_iter]


class _FakeEntryCompletion:
    def __init__(self) -> None:
        self.model = None
        self.text_column = None
        self.inline_completion = False
        self.popup_completion = False
        self.match_func = None

    def set_model(self, model) -> None:
        self.model = model

    def get_model(self):
        return self.model

    def set_text_column(self, column: int) -> None:
        self.text_column = column

    def set_inline_completion(self, value: bool) -> None:
        self.inline_completion = value

    def set_popup_completion(self, value: bool) -> None:
        self.popup_completion = value

    def set_match_func(self, func, user_data) -> None:
        self.match_func = lambda completion, key, tree_iter, _data: func(
            completion,
            key,
            tree_iter,
            user_data,
        )


class _FakeLabel:
    def __init__(self) -> None:
        self.text = ""
        self.markup = ""
        self.selectable = False
        self.xalign = 0.0

    def set_selectable(self, _value: bool) -> None:
        self.selectable = _value

    def set_xalign(self, value: float) -> None:
        self.xalign = value

    def set_markup(self, markup: str) -> None:
        self.markup = markup
        self.text = re.sub(r"<[^>]+>", "", markup)

    def get_text(self) -> str:
        return self.text


class _FakeBuildButton:
    def __init__(self, label: str = "") -> None:
        self.label = label
        self.tooltip = ""
        self.connected = None

    def set_tooltip_text(self, text: str) -> None:
        self.tooltip = text

    def connect(self, signal: str, callback, *args) -> None:
        self.connected = (signal, callback, args)


class _FakeBuildBox:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.children: list[object] = []
        self.size_request = None

    def set_margin_start(self, _value: int) -> None:
        return

    def set_margin_end(self, _value: int) -> None:
        return

    def set_margin_top(self, _value: int) -> None:
        return

    def set_margin_bottom(self, _value: int) -> None:
        return

    def set_size_request(self, width: int, height: int) -> None:
        self.size_request = (width, height)

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)


def _seed_popup_widgets(applet: UnitConverterApplet):
    applet._cat_combo = _FakeComboBoxText()
    for cat in get_categories():
        applet._cat_combo.append_text(cat.value)
    applet._cat_combo.set_active(applet._cat_idx)
    applet._from_combo = _FakeComboBoxText()
    applet._to_combo = _FakeComboBoxText()
    applet._entry = _FakeEntry("1")
    applet._result_label = _FakeLabel()
    applet._populate_unit_combos()
    applet._update_result()
    return {"category": applet._cat_idx}


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
            uc_state.urllib.request, "urlopen", lambda *a, **_kw: FakeResp()
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
            uc_state.urllib.request, "urlopen", lambda *a, **_kw: FakeResp()
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
    @staticmethod
    def _patch_popup(monkeypatch, applet: UnitConverterApplet, popup: _FakePopupWindow):
        monkeypatch.setattr(
            unitconverter_applet_mod.Gtk,
            "Dialog",
            lambda **_: popup,
        )
        monkeypatch.setattr(
            applet, "_build_popup_content", lambda: _seed_popup_widgets(applet)
        )

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

    def test_start_queues_currency_fetch(self):
        applet = _make_applet()
        applet._worker.run = MagicMock()

        applet.start(lambda: None)

        applet._worker.run.assert_called_once_with(
            name="currency-fetch",
            fn=uc_state.fetch_currency_rates,
            on_result=applet._on_currency_result,
        )

    def test_currency_result_updates_global_units_only_when_present(self, monkeypatch):
        applet = _make_applet()
        set_units = MagicMock()
        monkeypatch.setattr(unitconverter_applet_mod, "set_currency_units", set_units)
        units = (Unit("Euro", "EUR", 1.0),)

        assert applet._on_currency_result(units) is False
        set_units.assert_called_once_with(units)

        set_units.reset_mock()
        assert applet._on_currency_result(None) is False
        set_units.assert_not_called()

    def test_show_popup_builds_controls_and_reuses_window(self, monkeypatch):
        applet = _make_applet()
        fake_popup = _FakePopupWindow()
        self._patch_popup(monkeypatch, applet, fake_popup)
        applet._show_popup()
        first_popup = applet._popup
        first_child = first_popup.get_content_area().get_children()[0]
        assert applet._entry is not None
        assert applet._from_combo is not None
        assert applet._to_combo is not None

        applet._show_popup()

        assert applet._popup is first_popup
        assert applet._popup.get_content_area().get_children()[0] is not first_child
        assert fake_popup.presented is True
        assert fake_popup.resizable is False
        applet.stop()

    def test_show_popup_uses_real_dialog(self, monkeypatch):
        applet = _make_applet()
        dialog = _FakePopupWindow()
        monkeypatch.setattr(
            unitconverter_applet_mod.Gtk,
            "Dialog",
            lambda **_: dialog,
        )
        monkeypatch.setattr(applet, "_build_popup_content", lambda: "content")

        applet._show_popup()

        assert applet._popup is dialog
        assert dialog.connected is not None
        assert dialog.connected[0] == "delete-event"
        assert dialog.content.get_children() == ["content"]
        assert dialog.size == (unitconverter_applet_mod.POPUP_WIDTH_PX, -1)
        assert dialog.presented is True
        applet.stop()

    def test_build_popup_content_creates_real_controls_with_fakes(self, monkeypatch):
        applet = _make_applet()
        monkeypatch.setattr(
            unitconverter_applet_mod,
            "Gtk",
            SimpleNamespace(
                Box=_FakeBuildBox,
                ComboBoxText=_FakeComboBoxText,
                Button=_FakeBuildButton,
                Entry=_FakeEntry,
                Label=_FakeLabel,
                Orientation=SimpleNamespace(VERTICAL=1, HORIZONTAL=2),
            ),
        )
        monkeypatch.setattr(
            unitconverter_applet_mod,
            "entry_completion_combo",
            lambda **_: _FakeComboBoxText(),
        )

        box = unitconverter_applet_mod.UnitConverterApplet._build_popup_content(applet)

        assert isinstance(box, _FakeBuildBox)
        assert box.size_request == (unitconverter_applet_mod.POPUP_WIDTH_PX, -1)
        assert applet._cat_combo is not None
        assert len(applet._cat_combo.items) == len(get_categories())
        assert applet._from_combo is not None
        assert applet._to_combo is not None
        assert applet._entry is not None
        assert applet._entry.get_text() == "1"
        assert applet._entry.placeholder == "Value"
        assert applet._result_label is not None
        assert applet._result_label.selectable is True
        assert applet._result_label.xalign == 0.5

    def test_unit_combos_autocomplete_by_name_or_symbol(self, monkeypatch):
        monkeypatch.setattr(
            applet_popup_mod,
            "Gtk",
            SimpleNamespace(
                ComboBoxText=_FakeComboBoxText,
                EntryCompletion=_FakeEntryCompletion,
            ),
        )

        combo = applet_popup_mod.entry_completion_combo(
            matches=unitconverter_applet_mod._unit_label_matches
        )
        for unit in get_units(Category.LENGTH):
            combo.append_text(unitconverter_applet_mod._unit_label(unit))

        completion = combo.entry.completion
        km_index = [unit.symbol for unit in get_units(Category.LENGTH)].index("km")

        assert combo.entry_text_column == 0
        assert completion.text_column == 0
        assert completion.inline_completion is True
        assert completion.popup_completion is True
        assert completion.match_func(completion, "kilo", km_index, None) is True
        assert completion.match_func(completion, "km", km_index, None) is True
        assert completion.match_func(completion, "mile", km_index, None) is False
        combo.entry._callbacks["focus-in-event"](combo.entry, None)
        assert combo.entry.selected_region == (0, -1)

    def test_typed_unit_text_updates_selected_index(self):
        applet = _make_applet()
        _seed_popup_widgets(applet)
        applet.save_prefs = MagicMock()
        applet._cat_idx = get_categories().index(Category.LENGTH)
        applet._populate_unit_combos()
        assert applet._from_combo is not None
        km_index = [unit.symbol for unit in get_units(Category.LENGTH)].index("km")

        applet._from_combo.entry.set_text("km")
        applet._on_unit_changed(applet._from_combo)

        assert applet._from_idx == km_index
        applet.save_prefs.assert_called_once()

    def test_category_change_repopulates_and_saves(self):
        applet = _make_applet()
        _seed_popup_widgets(applet)
        applet.save_prefs = MagicMock()
        combo = MagicMock()
        combo.get_active.return_value = 2

        applet._on_category_changed(combo)

        assert applet._cat_idx == 2
        assert applet._from_idx == 0
        assert applet._to_idx >= 0
        applet.save_prefs.assert_called_once()

    def test_category_change_ignores_negative_active(self):
        applet = _make_applet()
        combo = MagicMock()
        combo.get_active.return_value = -1

        applet._on_category_changed(combo)

        assert applet._cat_idx == 0

    def test_swap_exchanges_active_units(self):
        applet = _make_applet()
        applet._from_combo = MagicMock()
        applet._to_combo = MagicMock()
        applet._from_combo.get_active.return_value = 0
        applet._to_combo.get_active.return_value = 1

        applet._on_swap(MagicMock())

        applet._from_combo.set_active.assert_called_once_with(1)
        applet._to_combo.set_active.assert_called_once_with(0)

    def test_swap_is_noop_without_combos(self):
        applet = _make_applet()
        applet._from_combo = None
        applet._to_combo = None

        applet._on_swap(MagicMock())

    def test_unit_change_clamps_indexes_and_saves(self):
        applet = _make_applet()
        _seed_popup_widgets(applet)
        applet.save_prefs = MagicMock()
        assert applet._from_combo is not None
        assert applet._to_combo is not None
        applet._from_combo.set_active(-1)
        applet._to_combo.set_active(-1)

        applet._on_unit_changed(applet._from_combo)

        assert applet._from_idx == 0
        assert applet._to_idx == 0
        applet.save_prefs.assert_called_once()

    def test_input_change_recomputes_result(self):
        applet = _make_applet()
        applet._update_result = MagicMock()

        applet._on_input_changed(MagicMock())

        applet._update_result.assert_called_once_with()

    def test_update_result_handles_missing_widgets(self):
        applet = _make_applet()
        applet._result_label = None
        applet._entry = None

        applet._update_result()

    def test_update_result_handles_invalid_number(self):
        applet = _make_applet()
        _seed_popup_widgets(applet)
        assert applet._entry is not None
        assert applet._result_label is not None
        applet._entry.set_text("not-a-number")

        applet._update_result()

        assert applet._result_label.get_text() == "Enter a number"

    def test_update_result_handles_empty_unit_list(self, monkeypatch):
        applet = _make_applet()
        _seed_popup_widgets(applet)
        assert applet._entry is not None
        assert applet._result_label is not None
        applet._entry.set_text("1")
        monkeypatch.setattr(unitconverter_applet_mod, "get_units", lambda _cat: ())

        applet._update_result()

        assert applet._result_label.get_text() == "No units available"

    def test_update_result_formats_conversion_output(self):
        applet = _make_applet()
        _seed_popup_widgets(applet)
        assert applet._entry is not None
        assert applet._result_label is not None
        assert applet._cat_combo is not None
        assert applet._from_combo is not None
        assert applet._to_combo is not None
        applet._cat_combo.set_active(get_categories().index(Category.LENGTH))
        applet._on_category_changed(applet._cat_combo)
        applet._entry.set_text("2")
        applet._from_combo.set_active(0)
        applet._to_combo.set_active(1)
        applet._on_unit_changed(applet._from_combo)

        assert applet._result_label.get_text()
        assert applet._popup is None

    def test_update_result_does_not_force_white_text_on_themed_popup(self):
        applet = _make_applet()
        applet._entry = MagicMock()
        applet._entry.get_text.return_value = "2"
        applet._result_label = MagicMock()

        applet._update_result()

        markup = applet._result_label.set_markup.call_args.args[0]
        assert 'color="white"' not in markup
        assert 'weight="bold"' in markup


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
