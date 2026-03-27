"""Tests for the separator applet."""

from types import SimpleNamespace

import docking.applets.separator.applet as separator_applet_mod
from docking.applets.identity import applet_id_from
from docking.applets.separator import (
    DEFAULT_SIZE,
    MAX_SIZE,
    MIN_SIZE,
    STEP,
    STYLE_LINE,
    STYLE_SPACE,
    SeparatorApplet,
)


class FakeMenu:
    def __init__(self) -> None:
        self.children: list[object] = []

    def append(self, child) -> None:
        self.children.append(child)

    def get_children(self):
        return list(self.children)


class FakeMenuItem:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._submenu = None
        self._signals: dict[str, list[object]] = {}

    def get_label(self) -> str:
        return self._label

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu

    def connect(self, signal: str, callback) -> None:
        self._signals.setdefault(signal, []).append(callback)


class FakeCheckMenuItem(FakeMenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label)
        self._active = False

    def set_draw_as_radio(self, _value: bool) -> None:
        return

    def set_active(self, active: bool) -> None:
        self._active = active

    def get_active(self) -> bool:
        return self._active


class FakeSeparatorMenuItem(FakeMenuItem):
    pass


class TestAppletIdFrom:
    def test_simple_applet(self):
        assert applet_id_from(desktop_id="applet://clock") == "clock"

    def test_separator_instance(self):
        assert applet_id_from(desktop_id="applet://separator#0") == "separator"

    def test_separator_high_instance(self):
        assert applet_id_from(desktop_id="applet://separator#42") == "separator"

    def test_no_instance_suffix(self):
        assert applet_id_from(desktop_id="applet://weather") == "weather"


class TestSeparatorApplet:
    def _fake_gtk(self, monkeypatch):
        fake_gtk = SimpleNamespace(
            Menu=FakeMenu,
            MenuItem=FakeMenuItem,
            CheckMenuItem=FakeCheckMenuItem,
            SeparatorMenuItem=FakeSeparatorMenuItem,
        )
        monkeypatch.setattr(separator_applet_mod, "Gtk", fake_gtk)

    def test_creates_with_icon(self):
        applet = SeparatorApplet(48)
        assert applet.item.icon is not None
        assert applet.item.name == "Separator"

    def test_default_gap(self):
        applet = SeparatorApplet(48)
        assert applet.item.main_size == DEFAULT_SIZE

    def test_icon_width_matches_gap(self):
        applet = SeparatorApplet(48)
        pixbuf = applet.create_icon(size=48)
        assert pixbuf is not None
        assert pixbuf.get_width() == DEFAULT_SIZE
        assert pixbuf.get_height() == 48

    def test_menu_has_increase_decrease(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        applet = SeparatorApplet(48)
        labels = [mi.get_label() for mi in applet.get_menu_items()]
        assert labels == ["Increase Gap", "Decrease Gap", "", "Style", "Invert Color"]

    def test_style_menu_has_line_and_space(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        applet = SeparatorApplet(48)
        style_item = next(
            mi for mi in applet.get_menu_items() if mi.get_label() == "Style"
        )
        submenu = style_item.get_submenu()
        assert submenu is not None
        assert [mi.get_label() for mi in submenu.get_children()] == ["Line", "Space"]

    def test_scroll_up_increases_gap(self):
        applet = SeparatorApplet(48)
        before = applet._gap
        applet.on_scroll(direction_up=True)
        assert applet._gap == before + STEP
        assert applet.item.main_size == applet._gap

    def test_scroll_down_decreases_gap(self):
        applet = SeparatorApplet(48)
        before = applet._gap
        applet.on_scroll(direction_up=False)
        assert applet._gap == before - STEP
        assert applet.item.main_size == applet._gap

    def test_gap_clamps_at_min(self):
        applet = SeparatorApplet(48)
        applet._gap = MIN_SIZE
        applet.on_scroll(direction_up=False)
        assert applet._gap == MIN_SIZE

    def test_gap_clamps_at_max(self):
        applet = SeparatorApplet(48)
        applet._gap = MAX_SIZE
        applet.on_scroll(direction_up=True)
        assert applet._gap == MAX_SIZE

    def test_desktop_id_can_be_overridden(self):
        applet = SeparatorApplet(48)
        applet.item.desktop_id = "applet://separator#5"
        assert applet.item.desktop_id == "applet://separator#5"

    def test_apply_prefs_clamps_loaded_gap_above_max(self):
        applet = SeparatorApplet(48)
        applet.item.desktop_id = "applet://separator#0"
        applet.load_instance_prefs = lambda: {"gap": MAX_SIZE + 100}

        applet.apply_prefs()

        assert applet._gap == MAX_SIZE
        assert applet.item.main_size == MAX_SIZE

    def test_apply_prefs_clamps_loaded_gap_below_min(self):
        applet = SeparatorApplet(48)
        applet.item.desktop_id = "applet://separator#0"
        applet.load_instance_prefs = lambda: {"gap": -999}

        applet.apply_prefs()

        assert applet._gap == MIN_SIZE
        assert applet.item.main_size == MIN_SIZE

    def test_apply_prefs_invalid_gap_falls_back_to_default(self):
        applet = SeparatorApplet(48)
        applet.item.desktop_id = "applet://separator#0"
        applet.load_instance_prefs = lambda: {"gap": "bad"}

        applet.apply_prefs()

        assert applet._gap == DEFAULT_SIZE
        assert applet.item.main_size == DEFAULT_SIZE

    def test_apply_prefs_loads_style_and_invert_color(self):
        applet = SeparatorApplet(48)
        applet.item.desktop_id = "applet://separator#0"
        applet.load_instance_prefs = lambda: {
            "gap": DEFAULT_SIZE,
            "style": STYLE_LINE,
            "invert_color": True,
        }

        applet.apply_prefs()

        assert applet._style == STYLE_LINE
        assert applet._invert_color is True
        assert applet.item.allow_zoom is False

    def test_invalid_style_falls_back_to_space(self):
        applet = SeparatorApplet(48)
        applet.item.desktop_id = "applet://separator#0"
        applet.load_instance_prefs = lambda: {"style": "bad"}

        applet.apply_prefs()

        assert applet._style == STYLE_SPACE
