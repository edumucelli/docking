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
from docking.ui.geometry import DockGeometryFrame, ItemGeometry, Rect


def _surface_context(width: int = 420, height: int = 90):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    return cairo.Context(surface)


def _layout():
    return [
        SimpleNamespace(x=0.0, scale=1.0, width=48.0),
        SimpleNamespace(x=70.0, scale=1.15, width=48.0),
    ]


def _frame(items, layout, *, cross_size: float = 90.0, offset: float = 0.0):
    item_geometries = []
    for item, li in zip(items, layout):
        width = int(li.width * li.scale)
        item_geometries.append(
            ItemGeometry(
                item=item,
                layout_item=li,
                draw_rect=Rect(int(li.x + offset), 10, max(width, 1), max(width, 1)),
                hover_rect=Rect(int(li.x + offset), 0, max(width, 1), 70),
                hit_rect=Rect(int(li.x + offset), 8, max(width, 1), 62),
                background_rect=Rect(int(li.x + offset), 48, max(width, 1), 22),
                anchor_x=float(li.x + offset + width / 2),
                anchor_y=10.0,
                scaled_size=float(width),
                main_pos=float(li.x + offset),
            )
        )
    return DockGeometryFrame(
        window_rect=Rect(0, 0, 420, 90),
        static_dock_rect=Rect(0, 0, 160, 70),
        cursor_rect=Rect(0, 0, 160, 70),
        background_rect=Rect(0, 48, 160, 21),
        layout=tuple(layout),
        item_geometries=tuple(item_geometries),
        local_cursor_main=0.0,
        zoomed_main_offset=offset,
        cross_size=cross_size,
    )


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
        layout = _layout()

        monkeypatch.setattr(
            renderer_mod,
            "build_geometry_frame",
            lambda **_kwargs: _frame([i1, i2], layout),
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

    def test_draw_content_uses_frame_background_rect_for_shelf(self, monkeypatch):
        renderer = renderer_mod.DockRenderer()
        theme = Theme.load("default", 48)
        config = SimpleNamespace(pos=Position.BOTTOM, icon_size=48)
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        model = MagicMock()
        model.visible_items.return_value = [item]
        layout = [SimpleNamespace(x=0.0, scale=1.0, width=48.0)]
        frame = _frame([item], layout)
        frame = DockGeometryFrame(
            window_rect=frame.window_rect,
            static_dock_rect=frame.static_dock_rect,
            cursor_rect=frame.cursor_rect,
            background_rect=Rect(24, 50, 120, 21),
            layout=frame.layout,
            item_geometries=frame.item_geometries,
            local_cursor_main=frame.local_cursor_main,
            zoomed_main_offset=frame.zoomed_main_offset,
            cross_size=frame.cross_size,
        )

        shelf_calls: list[dict[str, float]] = []
        monkeypatch.setattr(
            renderer_mod, "build_geometry_frame", lambda **_kwargs: frame
        )
        monkeypatch.setattr(
            renderer_mod,
            "draw_shelf_background",
            lambda **kwargs: shelf_calls.append(kwargs),
        )
        monkeypatch.setattr(renderer_mod.GLib, "get_monotonic_time", lambda: 100_000)
        renderer._draw_icon = MagicMock()
        renderer._draw_indicator = MagicMock()
        renderer._draw_active_glow = MagicMock()
        renderer._draw_urgent_glow = MagicMock()

        renderer._draw_content(
            cr=_surface_context(),
            width=420,
            height=90,
            model=model,
            config=config,
            theme=theme,
            cursor_main=12.0,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            zoom_progress=1.0,
            hovered_id="",
        )

        assert shelf_calls
        assert shelf_calls[0]["x"] == 24
        assert shelf_calls[0]["w"] == 120
        assert shelf_calls[0]["h"] == 21

    def test_draw_content_uses_separator_drawer_for_separator_items(self, monkeypatch):
        renderer = renderer_mod.DockRenderer()
        theme = Theme.load("default", 48)
        config = SimpleNamespace(
            pos=Position.BOTTOM,
            icon_size=48,
            applet_prefs={"separator#0": {"style": "line", "invert_color": False}},
        )
        item = DockItem(
            desktop_id="applet://separator#0",
            kind="applet",
            main_size=12,
            allow_zoom=False,
        )
        model = MagicMock()
        model.visible_items.return_value = [item]
        layout = [SimpleNamespace(x=0.0, scale=1.0, width=12.0)]

        monkeypatch.setattr(
            renderer_mod,
            "build_geometry_frame",
            lambda **_kwargs: _frame([item], layout),
        )
        monkeypatch.setattr(
            renderer_mod, "draw_shelf_background", lambda **kwargs: None
        )
        monkeypatch.setattr(renderer_mod.GLib, "get_monotonic_time", lambda: 100_000)

        renderer._draw_icon = MagicMock()
        renderer._draw_separator = MagicMock()

        cr = _surface_context()
        renderer._draw_content(
            cr=cr,
            width=420,
            height=90,
            model=model,
            config=config,
            theme=theme,
            cursor_main=10.0,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            zoom_progress=1.0,
            hovered_id="",
        )

        renderer._draw_icon.assert_not_called()
        renderer._draw_separator.assert_called_once()

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
