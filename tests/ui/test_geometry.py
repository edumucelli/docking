"""Tests for the shared dock geometry frame."""

from __future__ import annotations

from types import SimpleNamespace

from docking.core.position import Position
from docking.core.zoom import content_bounds
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.geometry import Rect, build_geometry_frame


def _config(pos: Position = Position.BOTTOM) -> SimpleNamespace:
    return SimpleNamespace(
        pos=pos,
        icon_size=48,
        zoom_percent=1.5,
        zoom_enabled=True,
    )


def _theme(distance_from_edge: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        distance_from_edge=distance_from_edge,
        item_padding=8,
        h_padding=12,
        top_padding=0,
        bottom_padding=4,
        shelf_height=21,
        stroke_width=1.0,
    )


class TestRect:
    def test_contains_uses_half_open_bounds(self):
        rect = Rect(10, 20, 30, 40)

        assert rect.contains(10, 20) is True
        assert rect.contains(39, 59) is True
        assert rect.contains(40, 59) is False
        assert rect.contains(39, 60) is False


class TestDockGeometryFrame:
    def test_build_frame_exposes_item_lookup_and_insert_index(self):
        items = [
            DockItem(desktop_id="firefox.desktop"),
            DockItem(desktop_id="code.desktop"),
        ]

        frame = build_geometry_frame(
            items=items,
            config=_config(),
            theme=_theme(),
            window_w=420,
            window_h=90,
            cursor_main=60.0,
            autohide_state=None,
        )

        first = frame.geometry_for_item(items[0])
        second = frame.geometry_for_item(items[1])

        assert first is not None
        assert second is not None
        assert frame.item_at_point(first.anchor_x, first.anchor_y) is items[0]
        assert frame.item_index_at_point(second.anchor_x, second.anchor_y) == 1
        assert frame.insertion_index_for_main(-1.0, pos=Position.BOTTOM) == 0
        assert frame.insertion_index_for_main(10_000.0, pos=Position.BOTTOM) == 2

    def test_hidden_frame_uses_trigger_strip_as_cursor_region(self):
        item = DockItem(desktop_id="firefox.desktop")

        frame = build_geometry_frame(
            items=[item],
            config=_config(),
            theme=_theme(distance_from_edge=6),
            window_w=420,
            window_h=90,
            cursor_main=-1.0,
            autohide_state=HideState.HIDDEN,
        )

        assert frame.cursor_rect.h == 8
        assert frame.cursor_rect.y == 82

    def test_item_hover_regions_partition_the_background_width(self):
        items = [
            DockItem(desktop_id="firefox.desktop"),
            DockItem(desktop_id="code.desktop"),
            DockItem(desktop_id="chrome.desktop"),
        ]

        frame = build_geometry_frame(
            items=items,
            config=_config(),
            theme=_theme(),
            window_w=420,
            window_h=90,
            cursor_main=120.0,
            autohide_state=None,
        )

        first = frame.item_geometries[0]
        middle = frame.item_geometries[1]
        last = frame.item_geometries[-1]

        assert first.hover_rect.x == frame.background_rect.x
        assert first.hover_rect.x + first.hover_rect.w == middle.hover_rect.x
        assert middle.hover_rect.x + middle.hover_rect.w == last.hover_rect.x
        assert last.hover_rect.x + last.hover_rect.w == (
            frame.background_rect.x + frame.background_rect.w
        )

    def test_background_click_zone_exists_between_hit_rects(self):
        items = [
            DockItem(desktop_id="firefox.desktop"),
            DockItem(desktop_id="code.desktop"),
            DockItem(desktop_id="chrome.desktop"),
        ]

        frame = build_geometry_frame(
            items=items,
            config=_config(),
            theme=_theme(),
            window_w=420,
            window_h=90,
            cursor_main=120.0,
            autohide_state=None,
        )

        background_y = frame.background_rect.y + frame.background_rect.h - 1
        gap_x = None
        for x in range(
            frame.background_rect.x,
            frame.background_rect.x + frame.background_rect.w,
        ):
            if frame.item_at_point(x, background_y) is None:
                gap_x = x
                break

        assert gap_x is not None
        assert frame.cursor_rect.contains(gap_x, background_y) is True
        assert frame.hover_item_at_point(gap_x, background_y) is not None

    def test_background_rect_tracks_visible_shelf_extent(self):
        items = [
            DockItem(desktop_id="firefox.desktop"),
            DockItem(desktop_id="code.desktop"),
            DockItem(desktop_id="chrome.desktop"),
        ]

        frame = build_geometry_frame(
            items=items,
            config=_config(),
            theme=_theme(),
            window_w=420,
            window_h=90,
            cursor_main=120.0,
            autohide_state=None,
        )

        assert frame.background_rect.y + frame.background_rect.h == (
            frame.static_dock_rect.y + frame.static_dock_rect.h
        )
        assert frame.background_rect.x <= frame.static_dock_rect.x
        assert (
            frame.background_rect.x + frame.background_rect.w
            >= frame.static_dock_rect.x + frame.static_dock_rect.w
        )
        for item_geometry in frame.item_geometries:
            assert item_geometry.background_rect.h > 0
            assert frame.background_rect.contains(
                item_geometry.background_rect.x,
                item_geometry.background_rect.y,
            )

    def test_static_dock_rect_uses_centered_content_bounds(self):
        items = [
            DockItem(desktop_id="firefox.desktop"),
            DockItem(desktop_id="code.desktop"),
            DockItem(desktop_id="chrome.desktop"),
        ]

        frame = build_geometry_frame(
            items=items,
            config=_config(),
            theme=_theme(),
            window_w=420,
            window_h=90,
            cursor_main=350.0,
            autohide_state=None,
        )

        left, right = content_bounds(
            layout=list(frame.layout),
            icon_size=48,
            h_padding=12,
            item_padding=8,
        )
        shelf_width = right - left
        expected_x = int((420 - shelf_width) / 2)

        assert frame.static_dock_rect.x == expected_x
        assert frame.static_dock_rect.w == int(shelf_width)

    def test_outer_edge_zoom_tapers_smoothly_on_both_sides(self):
        items = [DockItem(desktop_id=f"app{i}.desktop") for i in range(20)]

        left_scales: list[float] = []
        for cursor in range(406, 374, -2):
            frame = build_geometry_frame(
                items=items,
                config=_config(),
                theme=_theme(),
                window_w=1920,
                window_h=122,
                cursor_main=float(cursor),
                autohide_state=None,
            )
            left_scales.append(frame.item_geometries[0].layout_item.scale)

        right_scales: list[float] = []
        for cursor in [1510, 1520, 1530, 1540, 1545, 1550, 1555]:
            frame = build_geometry_frame(
                items=items,
                config=_config(),
                theme=_theme(),
                window_w=1920,
                window_h=122,
                cursor_main=float(cursor),
                autohide_state=None,
            )
            right_scales.append(frame.item_geometries[-1].layout_item.scale)

        assert all(a > b for a, b in zip(left_scales, left_scales[1:]))
        assert all(scale > 1.0 for scale in left_scales)
        assert left_scales[-1] > 1.0

        assert all(a > b for a, b in zip(right_scales, right_scales[1:]))
        assert all(scale > 1.0 for scale in right_scales)
