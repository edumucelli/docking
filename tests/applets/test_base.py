"""Tests for the base applet lifecycle contract."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import cairo

from docking.applets.base import (
    Applet,
    _fit_icon_label_layout,
    _icon_label_origin,
    _icon_label_outline_width,
    draw_icon_label,
)


class _DeferredInitApplet(Applet):
    id = "session"
    name = "Deferred Init"
    icon_name = "system-log-out"

    def __init__(self) -> None:
        self._label = ""
        self.render_calls = 0
        super().__init__(icon_size=48)
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
        applet = _DeferredInitApplet()

        assert applet.render_calls == 1
        assert applet.item.name == "Ready"
        assert applet.item.icon is None


class _BasicApplet(Applet):
    id = "session"
    name = "Basic"
    icon_name = "system-log-out"

    def __init__(self, config=None) -> None:
        self.render_count = 0
        super().__init__(icon_size=32, config=config)

    def create_icon(self, size: int):
        assert size == 32
        self.render_count += 1
        return object()

    def refresh_tooltip(self) -> None:
        self.item.name = f"Rendered {self.render_count}"


class TestAppletBaseHelpers:
    def test_load_prefs_reads_config_for_applet_id(self):
        config = MagicMock()
        config.applet_prefs = {"session": {"enabled": True}}
        applet = _BasicApplet(config=config)

        assert applet.load_prefs() == {"enabled": True}

    def test_load_prefs_without_config_returns_empty(self):
        applet = _BasicApplet()

        assert applet.load_prefs() == {}

    def test_save_prefs_updates_config_and_calls_save(self):
        config = MagicMock()
        config.applet_prefs = {}
        applet = _BasicApplet(config=config)

        applet.save_prefs({"foo": "bar"})

        assert config.applet_prefs["session"] == {"foo": "bar"}
        config.save.assert_called_once_with()

    def test_default_hooks_are_safe_and_present_notifies(self):
        applet = _BasicApplet()
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
