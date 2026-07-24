"""Tests for the base applet lifecycle contract."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import cairo
import pytest

import docking.applets.base as base_mod
from docking.applets.base import (
    ICON_SOURCE_DOCKING,
    ICON_SOURCE_PREF_KEY,
    ICON_SOURCE_SYSTEM,
    Applet,
    _fit_icon_label_layout,
    _icon_label_origin,
    _icon_label_outline_width,
    draw_icon_label,
)
from docking.core.config import Config
from docking.core.icons import IconSource


class _DeferredInitApplet(Applet):
    id = "session"
    name = "Deferred Init"
    icon_name = "system-log-out"

    def __init__(self, config: Config) -> None:
        self._label = ""
        self.render_calls = 0
        super().__init__(icon_size=48, config=config)
        self._label = "Ready"
        self.present()

    def create_icon(self, size: int):
        assert size == 48
        assert self._label == "Ready"
        self.render_calls += 1

    def refresh_tooltip(self) -> None:
        self.item.name = self._label


class TestAppletBaseLifecycle:
    def test_initial_presentation_waits_for_subclass_init(self):
        applet = _DeferredInitApplet(Config())

        assert applet.render_calls == 1
        assert applet.item.name == "Ready"
        assert applet.item.icon is None


class _BasicApplet(Applet):
    id = "session"
    name = "Basic"
    icon_name = "system-log-out"

    def __init__(self, config: Config) -> None:
        self.render_count = 0
        super().__init__(icon_size=32, config=config)

    def create_icon(self, size: int):
        assert size == 32
        self.render_count += 1
        return object()

    def refresh_tooltip(self) -> None:
        self.item.name = f"Rendered {self.render_count}"


class _SystemIconApplet(Applet):
    id = "session"
    name = "System Icon"
    icon_name = "system-log-out"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(self, config: Config) -> None:
        self.docking_icon = object()
        self.render_count = 0
        super().__init__(icon_size=32, config=config)

    def create_docking_icon(self, size: int):
        assert size == 32
        self.render_count += 1
        return self.docking_icon

    def system_icon_name(self) -> str:
        return "system-preferred"


class TestAppletBaseHelpers:
    def test_load_prefs_reads_config_for_applet_id(self):
        config = MagicMock()
        config.applet_prefs = {"session": {"enabled": True}}
        applet = _BasicApplet(config=config)

        assert applet.load_prefs() == {"enabled": True}

    def test_load_prefs_from_empty_config_returns_empty(self):
        applet = _BasicApplet(Config())

        assert applet.load_prefs() == {}

    def test_save_prefs_updates_config_and_calls_save(self):
        config = MagicMock()
        config.applet_prefs = {}
        applet = _BasicApplet(config=config)

        applet.save_prefs({"foo": "bar"})

        assert config.applet_prefs["session"] == {"foo": "bar"}
        config.save.assert_called_once_with()

    def test_default_hooks_are_safe_and_present_notifies(self):
        applet = _BasicApplet(Config())
        notify = MagicMock()

        applet.start(notify)
        applet.present()
        applet.on_clicked()
        applet.on_scroll(True)
        applet.apply_prefs()
        applet.stop()

        assert applet.get_menu_items() == []
        assert applet.item.name == "Rendered 1"
        assert applet.item.icon is not None
        notify.assert_called_once_with()

    def test_system_icon_applet_defaults_to_docking_icon(self):
        applet = _SystemIconApplet(Config())

        assert applet.icon_source() == ICON_SOURCE_DOCKING
        assert applet.create_icon(32) is applet.docking_icon
        assert applet.render_count == 1
        assert applet.item.icon_name == "system-log-out"

    def test_system_icon_applet_uses_theme_icon_when_selected(self, monkeypatch):
        icon = object()
        config = MagicMock()
        config.applet_prefs = {"session": {ICON_SOURCE_PREF_KEY: ICON_SOURCE_SYSTEM}}
        monkeypatch.setattr(base_mod, "load_theme_icon", lambda **_: icon)
        applet = _SystemIconApplet(config=config)

        assert applet.icon_source() == ICON_SOURCE_SYSTEM
        assert applet.create_icon(32) is icon
        assert applet.render_count == 0
        assert applet.item.icon_name == "system-preferred"

    def test_system_icon_applet_falls_back_to_docking_icon(self, monkeypatch):
        config = MagicMock()
        config.applet_prefs = {"session": {ICON_SOURCE_PREF_KEY: ICON_SOURCE_SYSTEM}}
        monkeypatch.setattr(base_mod, "load_theme_icon", lambda **_: None)
        applet = _SystemIconApplet(config=config)

        assert applet.create_icon(32) is applet.docking_icon
        assert applet.render_count == 1
        assert applet.item.icon_name == "system-log-out"

    def test_set_icon_source_persists_and_presents(self, monkeypatch):
        icon = object()
        config = MagicMock()
        config.applet_prefs = {"session": {"existing": True}}
        monkeypatch.setattr(base_mod, "load_theme_icon", lambda **_: icon)
        applet = _SystemIconApplet(config=config)

        applet.set_icon_source(ICON_SOURCE_SYSTEM)

        assert config.applet_prefs["session"] == {
            "existing": True,
            ICON_SOURCE_PREF_KEY: ICON_SOURCE_SYSTEM,
        }
        assert applet.item.icon is icon
        config.save.assert_called_once_with()


class TestDrawIconLabel:
    def test_long_label_shrinks_to_fit_max_width(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 48, 48)
        cr = cairo.Context(surface)

        _layout, logical, font_size = _fit_icon_label_layout(
            cr=cr,
            text="1234567890MB",
            max_width=34.0,
            initial_font_size=12,
            min_font_size=4,
        )

        assert font_size < 12
        assert logical.width <= 34.0 or font_size == 4

    def test_short_label_keeps_initial_font_size(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 48, 48)
        cr = cairo.Context(surface)

        _layout, logical, font_size = _fit_icon_label_layout(
            cr=cr,
            text="42",
            max_width=44.0,
            initial_font_size=12,
            min_font_size=4,
        )

        assert font_size == 12
        assert logical.width <= 44.0

    def test_origin_keeps_bottom_edge_stable_for_different_heights(self):
        first = SimpleNamespace(x=0, y=1, width=24, height=9)
        second = SimpleNamespace(x=0, y=3, width=32, height=6)

        _first_x, first_y = _icon_label_origin(
            size=48,
            logical=first,
            bottom_padding=2.0,
        )
        _second_x, second_y = _icon_label_origin(
            size=48,
            logical=second,
            bottom_padding=2.0,
        )

        assert first_y + first.y + first.height == 46.0
        assert second_y + second.y + second.height == 46.0

    def test_outline_width_tracks_final_font_size(self):
        assert _icon_label_outline_width(font_size=12) == 2.64
        assert _icon_label_outline_width(font_size=4) == 1.0

    def test_draw_icon_label_accepts_optional_width_and_tones(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 48, 48)
        cr = cairo.Context(surface)

        draw_icon_label(
            cr=cr,
            text="1234567890MB",
            size=48,
            max_width=34.0,
            fill_rgba=(0.9, 1.0, 0.9, 1.0),
            outline_rgba=(0.0, 0.0, 0.0, 0.75),
        )

    def test_draw_icon_label_empty_text_is_noop(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 48, 48)
        cr = cairo.Context(surface)
        # Should not raise
        draw_icon_label(cr=cr, text="", size=48)

    def test_fit_icon_label_falls_through_to_min_font_size(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 48, 48)
        cr = cairo.Context(surface)
        _layout, logical, font_size = _fit_icon_label_layout(
            cr=cr,
            text="a" * 100,
            max_width=10.0,
            initial_font_size=24,
            min_font_size=4,
        )
        assert font_size == 4  # Falls through to min font
        assert logical.width > 0


class TestAppletDefaultHooks:
    def test_create_docking_icon_raises_not_implemented(self):
        applet = _DeferredInitApplet(Config())
        with pytest.raises(NotImplementedError):
            applet.create_docking_icon(48)

    def test_system_icon_name_defaults_to_icon_name(self):
        applet = _DeferredInitApplet(Config())
        assert applet.system_icon_name() == applet.icon_name

    def test_icon_source_no_system_support_returns_docking(self):
        applet = _DeferredInitApplet(Config())
        assert applet.icon_source() == ICON_SOURCE_DOCKING

    def test_set_icon_source_no_system_support_returns_early(self):
        applet = _DeferredInitApplet(Config())
        # Should not raise or save
        applet.set_icon_source(ICON_SOURCE_SYSTEM)

    def test_set_icon_source_same_value_returns_early(self, monkeypatch):
        applet = _SystemIconApplet(Config())
        monkeypatch.setattr(
            applet, "load_prefs", lambda: {ICON_SOURCE_PREF_KEY: ICON_SOURCE_DOCKING}
        )
        # Should not raise
        applet.set_icon_source(ICON_SOURCE_DOCKING)

    def test_set_popup_anchor(self):
        applet = _DeferredInitApplet(Config())
        anchor = MagicMock()
        applet.set_popup_anchor(anchor)
        assert applet._popup_anchor is anchor

    def test_accepts_drop_uris_defaults_false(self):
        applet = _DeferredInitApplet(Config())
        assert applet.accepts_drop_uris() is False

    def test_on_drop_uris_default_returns_false(self):
        applet = _DeferredInitApplet(Config())
        assert applet.on_drop_uris(["file:///test.txt"]) is False

    def test_stack_content_defaults_to_none(self):
        applet = _DeferredInitApplet(Config())

        assert applet.stack_content(48) is None

    def test_set_services_default_is_noop(self):
        applet = _DeferredInitApplet(Config())
        services = MagicMock()
        # Should not raise
        applet.set_services(services)

    def test_on_scroll_default_is_noop(self):
        applet = _DeferredInitApplet(Config())
        # Should not raise
        applet.on_scroll(direction_up=True)

    def test_refresh_tooltip_default_is_noop(self):
        applet = _DeferredInitApplet(Config())
        # Should not raise
        applet.refresh_tooltip()
