"""Integration-style tests for DockRenderer draw pipeline."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import cairo
import pytest

import docking.ui.renderer as renderer_mod
from docking.core.position import Position
from docking.core.theme import Theme
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.geometry import (
    DockGeometryFrame,
    ItemGeometry,
    Rect,
    build_geometry_frame,
)


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
    for item, li in zip(items, layout, strict=True):
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
    bg = Rect(0, 48, 160, 21)
    return DockGeometryFrame(
        window_rect=Rect(0, 0, 420, 90),
        static_dock_rect=Rect(0, 0, 160, 70),
        cursor_rect=Rect(0, 0, 160, 70),
        background_rect=bg,
        layout=tuple(layout),
        item_geometries=tuple(item_geometries),
        local_cursor_main=0.0,
        zoomed_main_offset=offset,
        cross_size=cross_size,
        shelf_main_pos=float(bg.x),
        shelf_main_extent=float(bg.w),
        shelf_cross_pos=float(bg.y),
        shelf_cross_extent=float(bg.h),
        shelf_as_bottom_y=float(bg.y),
    )


class TestRendererDrawEntry:
    def test_draw_invokes_offscreen_content_pipeline(self):
        # Given
        renderer = renderer_mod.DockRenderer()
        renderer._draw_content = MagicMock()
        widget = MagicMock()
        widget.get_allocation.return_value = SimpleNamespace(width=420, height=90)
        cr = _surface_context()
        config = SimpleNamespace()
        theme = MagicMock()
        frame = _frame([], [])

        # When
        renderer.draw(
            cr=cr,
            widget=widget,
            frame=frame,
            config=config,
            theme=theme,
        )
        # Then
        renderer._draw_content.assert_called_once()

    def test_draw_reuses_offscreen_surface_for_same_allocation(self):
        renderer = renderer_mod.DockRenderer()
        renderer._draw_content = MagicMock()
        widget = MagicMock()
        widget.get_allocation.return_value = SimpleNamespace(width=420, height=90)
        cr = _surface_context()
        frame = _frame([], [])

        renderer.draw(
            cr=cr,
            widget=widget,
            frame=frame,
            config=SimpleNamespace(),
            theme=MagicMock(),
        )
        first_surface = renderer._cache.offscreen_surface

        renderer.draw(
            cr=cr,
            widget=widget,
            frame=frame,
            config=SimpleNamespace(),
            theme=MagicMock(),
        )

        assert first_surface is not None
        assert renderer._cache.offscreen_surface is first_surface
        assert renderer._cache.offscreen_surface.surface is first_surface.surface

    def test_draw_recreates_offscreen_surface_when_allocation_changes(self):
        renderer = renderer_mod.DockRenderer()
        renderer._draw_content = MagicMock()
        widget = MagicMock()
        cr = _surface_context()
        frame = _frame([], [])

        widget.get_allocation.return_value = SimpleNamespace(width=420, height=90)
        renderer.draw(
            cr=cr,
            widget=widget,
            frame=frame,
            config=SimpleNamespace(),
            theme=MagicMock(),
        )
        first_surface = renderer._cache.offscreen_surface

        widget.get_allocation.return_value = SimpleNamespace(width=480, height=90)
        renderer.draw(
            cr=cr,
            widget=widget,
            frame=frame,
            config=SimpleNamespace(),
            theme=MagicMock(),
        )

        assert first_surface is not None
        assert renderer._cache.offscreen_surface is not first_surface
        assert renderer._cache.offscreen_surface.surface is not first_surface.surface


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
            frame=_frame([i1, i2], layout),
            config=config,
            theme=theme,
            hide_offset=1.0,
            drag_index=-1,
            drop_insert_index=1,
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
        custom_bg = Rect(24, 50, 120, 21)
        frame = DockGeometryFrame(
            window_rect=frame.window_rect,
            static_dock_rect=frame.static_dock_rect,
            cursor_rect=frame.cursor_rect,
            background_rect=custom_bg,
            layout=frame.layout,
            item_geometries=frame.item_geometries,
            local_cursor_main=frame.local_cursor_main,
            zoomed_main_offset=frame.zoomed_main_offset,
            cross_size=frame.cross_size,
            shelf_main_pos=float(custom_bg.x),
            shelf_main_extent=float(custom_bg.w),
            shelf_cross_pos=float(custom_bg.y),
            shelf_cross_extent=float(custom_bg.h),
            shelf_as_bottom_y=float(custom_bg.y),
        )

        shelf_calls: list[dict[str, float]] = []
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
            frame=frame,
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="",
        )

        assert shelf_calls
        assert shelf_calls[0]["x"] == 24
        assert shelf_calls[0]["w"] == 120
        assert shelf_calls[0]["h"] == 21

    def test_draw_content_dispatches_badge_and_progress_overlays(self, monkeypatch):
        renderer = renderer_mod.DockRenderer()
        theme = Theme.load("default", 48)
        config = SimpleNamespace(pos=Position.BOTTOM, icon_size=48)
        item = DockItem(
            desktop_id="firefox.desktop",
            is_running=True,
            badge_count=5,
            badge_visible=True,
            progress=0.4,
            progress_visible=True,
        )
        layout = [SimpleNamespace(x=0.0, scale=1.0, width=48.0)]

        monkeypatch.setattr(
            renderer_mod, "draw_shelf_background", lambda **kwargs: None
        )
        monkeypatch.setattr(renderer_mod.GLib, "get_monotonic_time", lambda: 100_000)
        renderer._draw_icon = MagicMock()
        renderer._draw_indicator = MagicMock()
        renderer._draw_badge = MagicMock()
        renderer._draw_progress = MagicMock()

        renderer._draw_content(
            cr=_surface_context(),
            frame=_frame([item], layout),
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="",
        )

        renderer._draw_badge.assert_called_once()
        renderer._draw_progress.assert_called_once()

    def test_draw_content_hides_shelf_when_dock_is_hidden_with_gap(self, monkeypatch):
        renderer = renderer_mod.DockRenderer()
        theme = replace(Theme.load("default", 48), distance_from_edge=6)
        config = SimpleNamespace(pos=Position.BOTTOM, icon_size=48)
        frame = build_geometry_frame(
            items=[DockItem(desktop_id="firefox.desktop", is_running=True)],
            config=SimpleNamespace(
                pos=Position.BOTTOM,
                icon_size=48,
                zoom_percent=1.5,
                zoom_enabled=True,
            ),
            theme=theme,
            window_w=420,
            window_h=82,
            cursor_main=-1.0,
            autohide_state=HideState.HIDDEN,
            hide_offset=1.0,
        )
        shelf_calls: list[dict[str, float]] = []
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
            frame=frame,
            config=config,
            theme=theme,
            hide_offset=1.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="",
        )

        assert shelf_calls
        assert shelf_calls[0]["y"] >= frame.window_rect.h

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
            renderer_mod, "draw_shelf_background", lambda **kwargs: None
        )
        monkeypatch.setattr(renderer_mod.GLib, "get_monotonic_time", lambda: 100_000)

        renderer._draw_icon = MagicMock()
        renderer._draw_separator = MagicMock()

        cr = _surface_context()
        renderer._draw_content(
            cr=cr,
            frame=_frame([item], layout),
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
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
            frame=_frame([], []),
            config=config,
            theme=theme,
            hide_offset=0.0,
            drag_index=-1,
            drop_insert_index=-1,
            hovered_id="",
        )
        # Then
        # When
        assert renderer.smooth_shelf_w == 0.0


class TestRendererHelpers:
    def test_icon_surface_for_item_caches_surface_until_icon_changes(self, monkeypatch):
        renderer = renderer_mod.DockRenderer()
        item = DockItem(desktop_id="firefox.desktop")
        first_icon = SimpleNamespace(get_width=lambda: 64, get_height=lambda: 64)
        second_icon = SimpleNamespace(get_width=lambda: 64, get_height=lambda: 64)
        item.icon = first_icon
        created_surfaces = [object(), object()]
        pixbuf_surface = MagicMock(side_effect=created_surfaces)
        monkeypatch.setattr(renderer, "_pixbuf_surface", pixbuf_surface)

        first = renderer._icon_surface_for_item(item=item)
        second = renderer._icon_surface_for_item(item=item)
        item.icon = second_icon
        third = renderer._icon_surface_for_item(item=item)

        assert first is created_surfaces[0]
        assert second is first
        assert third is created_surfaces[1]
        assert pixbuf_surface.call_count == 2

    def test_draw_icon_idle_path_uses_source_surface_without_temp_allocation(
        self, monkeypatch
    ):
        renderer = renderer_mod.DockRenderer()
        source_surface = SimpleNamespace(get_width=lambda: 64, get_height=lambda: 64)
        monkeypatch.setattr(
            renderer,
            "_icon_surface_for_item",
            lambda **_kwargs: source_surface,
        )
        monkeypatch.setattr(
            renderer_mod.cairo,
            "ImageSurface",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("idle path should not allocate a temp icon surface")
            ),
        )
        cr = MagicMock()
        item = DockItem(desktop_id="firefox.desktop")
        li = SimpleNamespace(scale=1.0)

        renderer._draw_icon(
            cr=cr,
            item=item,
            config=SimpleNamespace(),
            li=li,
            base_size=48,
            x=10.0,
            y=12.0,
            lighten=0.0,
            darken=0.0,
        )

        cr.set_source_surface.assert_called_once_with(source_surface, 0, 0)

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
