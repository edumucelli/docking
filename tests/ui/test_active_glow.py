"""Tests for the active-glow shape dispatch and tint resolution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import cairo
import pytest

import docking.ui.renderer as renderer_mod
from docking.core.theme import ActiveShape, ActiveTint, Theme
from docking.platform.model import DockItem
from docking.ui.geometry import (
    DockGeometryFrame,
    ItemGeometry,
    Rect,
)
from docking.ui.renderer import RenderState


def _surface_context(width: int = 200, height: int = 80):
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    return cairo.Context(surface)


def _frame_with_active_item(item: DockItem) -> DockGeometryFrame:
    li = SimpleNamespace(x=0.0, scale=1.0, width=48.0)
    geometry = ItemGeometry(
        item=item,
        layout_item=li,
        draw_rect=Rect(0, 10, 48, 48),
        hover_rect=Rect(0, 0, 48, 70),
        hit_rect=Rect(0, 8, 48, 62),
        background_rect=Rect(0, 48, 48, 21),
        anchor_x=24.0,
        anchor_y=10.0,
        scaled_size=48.0,
        main_pos=0.0,
    )
    bg = Rect(0, 48, 160, 21)
    return DockGeometryFrame(
        window_rect=Rect(0, 0, 200, 80),
        static_dock_rect=Rect(0, 0, 160, 70),
        cursor_rect=Rect(0, 0, 160, 70),
        background_rect=bg,
        layout=(li,),
        item_geometries=(geometry,),
        local_cursor_main=0.0,
        zoomed_main_offset=0.0,
        cross_size=80.0,
        shelf_main_pos=float(bg.x),
        shelf_main_extent=float(bg.w),
        shelf_cross_pos=float(bg.y),
        shelf_cross_extent=float(bg.h),
        shelf_as_bottom_y=float(bg.y),
    )


def _make_active_item() -> DockItem:
    item = DockItem(desktop_id="firefox.desktop", is_running=True)
    item.is_active = True
    return item


class TestActiveGlowDispatch:
    """``_draw_active_glow`` routes to the shape-specific drawer."""

    def test_linear_shape_calls_linear_drawer(self):
        renderer = renderer_mod.DockRenderer()
        renderer._draw_active_linear = MagicMock()
        renderer._draw_active_radial = MagicMock()
        renderer._draw_active_flat = MagicMock()
        li = SimpleNamespace(x=0.0, scale=1.0, width=48.0)
        renderer._draw_active_glow(
            cr=_surface_context(),
            li=li,
            icon_size=48,
            icon_offset=0.0,
            bg_y=48.0,
            bg_height=21.0,
            shelf_x=0.0,
            shelf_w=160.0,
            color=(0.5, 0.5, 0.5, 1.0),
            shape=ActiveShape.LINEAR,
            glow_opacity=0.6,
        )
        assert renderer._draw_active_linear.call_count == 1
        assert renderer._draw_active_radial.call_count == 0
        assert renderer._draw_active_flat.call_count == 0

    def test_radial_shape_calls_radial_drawer(self):
        renderer = renderer_mod.DockRenderer()
        renderer._draw_active_linear = MagicMock()
        renderer._draw_active_radial = MagicMock()
        renderer._draw_active_flat = MagicMock()
        li = SimpleNamespace(x=0.0, scale=1.0, width=48.0)
        renderer._draw_active_glow(
            cr=_surface_context(),
            li=li,
            icon_size=48,
            icon_offset=0.0,
            bg_y=48.0,
            bg_height=21.0,
            shelf_x=0.0,
            shelf_w=160.0,
            color=(0.5, 0.5, 0.5, 1.0),
            shape=ActiveShape.RADIAL,
            glow_opacity=0.6,
        )
        assert renderer._draw_active_radial.call_count == 1
        assert renderer._draw_active_linear.call_count == 0
        assert renderer._draw_active_flat.call_count == 0

    def test_flat_shape_calls_flat_drawer(self):
        renderer = renderer_mod.DockRenderer()
        renderer._draw_active_linear = MagicMock()
        renderer._draw_active_radial = MagicMock()
        renderer._draw_active_flat = MagicMock()
        li = SimpleNamespace(x=0.0, scale=1.0, width=48.0)
        renderer._draw_active_glow(
            cr=_surface_context(),
            li=li,
            icon_size=48,
            icon_offset=0.0,
            bg_y=48.0,
            bg_height=21.0,
            shelf_x=0.0,
            shelf_w=160.0,
            color=(0.5, 0.5, 0.5, 1.0),
            shape=ActiveShape.FLAT,
            glow_opacity=0.6,
        )
        assert renderer._draw_active_flat.call_count == 1
        assert renderer._draw_active_linear.call_count == 0
        assert renderer._draw_active_radial.call_count == 0

    def test_zero_width_band_skips_drawer(self):
        renderer = renderer_mod.DockRenderer()
        renderer._draw_active_linear = MagicMock()
        li = SimpleNamespace(x=500.0, scale=1.0, width=48.0)
        renderer._draw_active_glow(
            cr=_surface_context(),
            li=li,
            icon_size=48,
            icon_offset=0.0,
            bg_y=48.0,
            bg_height=21.0,
            shelf_x=0.0,
            shelf_w=10.0,
            color=(0.5, 0.5, 0.5, 1.0),
            shape=ActiveShape.LINEAR,
            glow_opacity=0.6,
        )
        assert renderer._draw_active_linear.call_count == 0


class TestActiveGlowAlphaMultiplication:
    """``color.a`` multiplies ``glow_opacity`` per Docking convention."""

    def test_linear_drawer_multiplies_color_alpha_into_gradient_stop(self, monkeypatch):
        captured = []

        class _RecordingGradient:
            def __init__(self, *args, **kwargs):
                pass

            def add_color_stop_rgba(self, offset, r, g, b, a):
                captured.append((offset, a))

        monkeypatch.setattr(renderer_mod.cairo, "LinearGradient", _RecordingGradient)
        cr = MagicMock()
        renderer_mod.DockRenderer._draw_active_linear(
            cr=cr,
            left=0.0,
            right=48.0,
            bg_y=0.0,
            bg_height=21.0,
            color=(0.5, 0.5, 0.5, 0.5),
            glow_opacity=0.8,
        )
        far_stops = [stop for stop in captured if stop[0] == 1]
        assert far_stops, "expected a stop at offset=1"
        # Alpha at the icon-side edge = glow_opacity * color.a = 0.8 * 0.5 = 0.4
        assert far_stops[-1][1] == pytest.approx(0.4)

    def test_radial_drawer_multiplies_color_alpha_into_center_stop(self, monkeypatch):
        captured = []

        class _RecordingGradient:
            def __init__(self, *args, **kwargs):
                pass

            def add_color_stop_rgba(self, offset, r, g, b, a):
                captured.append((offset, a))

        monkeypatch.setattr(renderer_mod.cairo, "RadialGradient", _RecordingGradient)
        cr = MagicMock()
        renderer_mod.DockRenderer._draw_active_radial(
            cr=cr,
            left=0.0,
            right=48.0,
            bg_y=0.0,
            bg_height=21.0,
            color=(0.5, 0.5, 0.5, 0.5),
            glow_opacity=0.8,
        )
        center_stops = [stop for stop in captured if stop[0] == 0]
        assert center_stops, "expected a stop at offset=0"
        assert center_stops[-1][1] == pytest.approx(0.4)

    def test_flat_drawer_multiplies_color_alpha_into_fill_alpha(self):
        cr = MagicMock()
        renderer_mod.DockRenderer._draw_active_flat(
            cr=cr,
            left=0.0,
            right=48.0,
            bg_y=0.0,
            bg_height=21.0,
            color=(0.5, 0.5, 0.5, 0.5),
            glow_opacity=0.8,
        )
        # set_source_rgba(r, g, b, glow_opacity * a)
        last_call = cr.set_source_rgba.call_args
        assert last_call.args[3] == pytest.approx(0.4)


class TestActiveGlowTintAtCallSite:
    """The draw_content loop resolves color based on the theme's active_tint."""

    def test_icon_tint_uses_cached_icon_color(self, monkeypatch):
        renderer = renderer_mod.DockRenderer()
        theme = Theme.load("default", 48).__class__(
            **{
                **Theme.load("default", 48).__dict__,
                "active_tint": ActiveTint.ICON,
                "active_shape": ActiveShape.LINEAR,
                "active_color": (1.0, 0.0, 0.0, 1.0),
            }
        )
        item = _make_active_item()
        item.icon = SimpleNamespace(get_width=lambda: 64, get_height=lambda: 64)
        monkeypatch.setattr(
            renderer._cache,
            "icon_color_for",
            lambda item: (0.1, 0.2, 0.3),
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

        config = SimpleNamespace(
            pos=__import__(
                "docking.core.position", fromlist=["Position"]
            ).Position.BOTTOM,
            icon_size=48,
            show_window_count_numbers=False,
            additional_distance_from_edge=0,
        )
        renderer._draw_content(
            cr=_surface_context(),
            frame=_frame_with_active_item(item),
            config=config,
            theme=theme,
            state=RenderState(
                hide_offset=0.0,
                drag_index=-1,
                drop_insert_index=-1,
                hovered_id="",
            ),
        )

        assert renderer._draw_active_glow.call_count == 1
        call = renderer._draw_active_glow.call_args
        assert call.kwargs["color"] == (0.1, 0.2, 0.3, 1.0)
        assert call.kwargs["shape"] is ActiveShape.LINEAR

    def test_drop_target_glow_is_pinned_to_linear_regardless_of_theme_shape(
        self, monkeypatch
    ):
        """Drop affordance must stay linear even when the theme picks radial/flat."""
        renderer = renderer_mod.DockRenderer()
        base = Theme.load("default", 48)
        # Theme picks RADIAL for the active glow.
        theme = base.__class__(
            **{
                **base.__dict__,
                "active_tint": ActiveTint.THEME,
                "active_shape": ActiveShape.RADIAL,
                "active_color": (0.8, 0.2, 0.6, 1.0),
            }
        )
        # The hovered item is the drop target, but not active.
        item = DockItem(desktop_id="firefox.desktop", is_running=True)
        item.icon = SimpleNamespace(get_width=lambda: 64, get_height=lambda: 64)

        monkeypatch.setattr(
            renderer._cache, "icon_color_for", lambda item: (0.1, 0.2, 0.3)
        )
        monkeypatch.setattr(
            renderer_mod, "draw_shelf_background", lambda **kwargs: None
        )
        monkeypatch.setattr(renderer_mod.GLib, "get_monotonic_time", lambda: 100_000)
        renderer._draw_icon = MagicMock()
        renderer._draw_indicator = MagicMock()
        renderer._draw_active_glow = MagicMock()
        renderer._draw_urgent_glow = MagicMock()

        config = SimpleNamespace(
            pos=__import__(
                "docking.core.position", fromlist=["Position"]
            ).Position.BOTTOM,
            icon_size=48,
            show_window_count_numbers=False,
            additional_distance_from_edge=0,
        )
        renderer._draw_content(
            cr=_surface_context(),
            frame=_frame_with_active_item(item),
            config=config,
            theme=theme,
            state=RenderState(
                hide_offset=0.0,
                drag_index=-1,
                drop_insert_index=-1,
                hovered_id="",
                drop_target_id="firefox.desktop",
            ),
        )

        # The active item is not active (item.is_active==False), so only the
        # drop-target branch invokes _draw_active_glow. It must use LINEAR
        # and the green drop color, not theme.active_shape or active_color.
        calls = renderer._draw_active_glow.call_args_list
        assert len(calls) == 1
        kwargs = calls[0].kwargs
        assert kwargs["shape"] is ActiveShape.LINEAR
        assert kwargs["color"] == (0.2, 0.8, 0.3, 1.0)

    def test_theme_tint_uses_theme_active_color(self, monkeypatch):
        renderer = renderer_mod.DockRenderer()
        base = Theme.load("default", 48)
        theme = base.__class__(
            **{
                **base.__dict__,
                "active_tint": ActiveTint.THEME,
                "active_shape": ActiveShape.RADIAL,
                "active_color": (0.8, 0.2, 0.6, 0.9),
            }
        )
        item = _make_active_item()
        item.icon = SimpleNamespace(get_width=lambda: 64, get_height=lambda: 64)
        icon_color_for = MagicMock()
        monkeypatch.setattr(renderer._cache, "icon_color_for", icon_color_for)
        monkeypatch.setattr(
            renderer_mod, "draw_shelf_background", lambda **kwargs: None
        )
        monkeypatch.setattr(renderer_mod.GLib, "get_monotonic_time", lambda: 100_000)
        renderer._draw_icon = MagicMock()
        renderer._draw_indicator = MagicMock()
        renderer._draw_active_glow = MagicMock()
        renderer._draw_urgent_glow = MagicMock()

        config = SimpleNamespace(
            pos=__import__(
                "docking.core.position", fromlist=["Position"]
            ).Position.BOTTOM,
            icon_size=48,
            show_window_count_numbers=False,
            additional_distance_from_edge=0,
        )
        renderer._draw_content(
            cr=_surface_context(),
            frame=_frame_with_active_item(item),
            config=config,
            theme=theme,
            state=RenderState(
                hide_offset=0.0,
                drag_index=-1,
                drop_insert_index=-1,
                hovered_id="",
            ),
        )

        assert renderer._draw_active_glow.call_count == 1
        call = renderer._draw_active_glow.call_args
        assert call.kwargs["color"] == (0.8, 0.2, 0.6, 0.9)
        assert call.kwargs["shape"] is ActiveShape.RADIAL
        # Theme tint should not pay the icon-averaging cost.
        assert icon_color_for.call_count == 0
