"""Integration-style tests for DockRenderer draw pipeline."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import cairo
import pytest

import docking.ui.renderer as renderer_mod
from docking.core.position import Position
from docking.core.theme import Theme
from docking.platform.model import DockItem


def _surface_context(width: int = 420, height: int = 90):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    return cairo.Context(surface)


def _layout():
    return [
        SimpleNamespace(x=0.0, scale=1.0, width=48.0),
        SimpleNamespace(x=70.0, scale=1.15, width=48.0),
    ]


class TestRendererDrawEntry:
    def test_draw_invokes_offscreen_content_pipeline(self):
        # Given
        renderer = renderer_mod.DockRenderer()
        renderer._draw_content = MagicMock()
        widget = MagicMock()
        widget.get_allocation.return_value = SimpleNamespace(width=420, height=90)
        cr = _surface_context()
        model = MagicMock()
        config = SimpleNamespace()
        theme = MagicMock()

        # When
        renderer.draw(
            cr=cr,
            widget=widget,
            model=model,
            config=config,
            theme=theme,
            cursor_main=30.0,
        )
        # Then
        renderer._draw_content.assert_called_once()


class TestRendererContentFlow:
    def test_draw_content_runs_icons_indicators_and_urgent_glow(self, monkeypatch):
        # Given
        renderer = renderer_mod.DockRenderer()
        theme = Theme.load("default", 48)
        config = SimpleNamespace(pos=Position.BOTTOM, icon_size=48)
        i1 = DockItem(
            desktop_id="firefox.desktop",
            is_active=True,
            is_running=True,
            is_urgent=True,
            instance_count=2,
            last_clicked=1,
            last_launched=1,
            last_urgent=1,
        )
        i2 = DockItem(
            desktop_id="code.desktop",
            is_running=True,
            instance_count=1,
            last_clicked=1,
            last_launched=1,
        )
        model = MagicMock()
        model.visible_items.return_value = [i1, i2]

        monkeypatch.setattr(
            renderer_mod, "compute_layout", lambda *args, **kwargs: _layout()
        )
        monkeypatch.setattr(
            renderer_mod, "content_bounds", lambda **kwargs: (0.0, 140.0)
        )
        monkeypatch.setattr(
            renderer_mod, "draw_shelf_background", lambda **kwargs: None
        )
        monkeypatch.setattr(
            renderer_mod, "average_icon_color", lambda pixbuf: (0.9, 0.4, 0.2)
        )
        monkeypatch.setattr(renderer_mod.GLib, "get_monotonic_time", lambda: 100_000)

        renderer._draw_icon = MagicMock()
        renderer._draw_indicator = MagicMock()
        renderer._draw_active_glow = MagicMock()
        renderer._draw_urgent_glow = MagicMock()

        cr = _surface_context()
        renderer._draw_content(
            cr=cr,
            width=420,
            height=90,
            model=model,
            config=config,
            theme=theme,
            cursor_main=50.0,
            hide_offset=1.0,
            drag_index=-1,
            drop_insert_index=1,
            zoom_progress=1.0,
            hovered_id="firefox.desktop",
        )

        # Then
        # When
        assert renderer._draw_icon.call_count == 2
        assert renderer._draw_indicator.call_count == 2
        assert renderer._draw_active_glow.call_count >= 1
        assert renderer._draw_urgent_glow.call_count >= 1
        assert "firefox.desktop" in renderer._hover_lighten
        assert renderer.smooth_shelf_w > 0

    def test_draw_content_returns_early_for_empty_items(self):
        # Given
        renderer = renderer_mod.DockRenderer()
        model = MagicMock()
        model.visible_items.return_value = []
        config = SimpleNamespace(pos=Position.BOTTOM, icon_size=48)
        theme = Theme.load("default", 48)
        cr = _surface_context()

        renderer._draw_content(
            cr=cr,
            width=400,
            height=80,
            model=model,
            config=config,
            theme=theme,
            cursor_main=10,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            zoom_progress=1.0,
            hovered_id="",
        )
        # Then
        # When
        assert renderer.smooth_shelf_w == 0.0


class TestRendererHelpers:
    def test_compute_dock_size_uses_custom_main_size(self):
        # Given
        renderer = renderer_mod.DockRenderer()
        model = MagicMock()
        model.visible_items.return_value = [
            DockItem(desktop_id="a.desktop", main_size=40),
            DockItem(desktop_id="b.desktop", main_size=0),
        ]
        config = SimpleNamespace(icon_size=48)
        theme = Theme.load("default", 48)

        width, height = renderer.compute_dock_size(
            model=model, config=config, theme=theme
        )
        # Then
        # When
        assert width > 0
        assert height > 0

    @pytest.mark.parametrize(
        "pos", [Position.BOTTOM, Position.TOP, Position.LEFT, Position.RIGHT]
    )
    def test_apply_shelf_transform_handles_all_positions(self, pos):
        # Given
        cr = _surface_context()
        renderer_mod.DockRenderer._apply_shelf_transform(
            cr=cr,
            pos=pos,
            width=300,
            height=80,
            main_size=300,
            cross_size=80,
            # Then
            # When
        )

    @pytest.mark.parametrize(
        "pos", [Position.BOTTOM, Position.TOP, Position.LEFT, Position.RIGHT]
    )
    def test_draw_indicator_handles_all_positions(self, pos):
        # Given
        cr = _surface_context()
        theme = Theme.load("default", 48)
        item = DockItem(desktop_id="x.desktop", instance_count=2, is_active=True)
        li = SimpleNamespace(x=10.0, scale=1.0)
        renderer_mod.DockRenderer._draw_indicator(
            cr=cr,
            item=item,
            li=li,
            base_size=48,
            main_pos=5.0,
            cross_size=80.0,
            hide_cross=0.0,
            theme=theme,
            pos=pos,
            # Then
            # When
        )
