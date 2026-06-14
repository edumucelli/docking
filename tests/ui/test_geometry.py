"""Tests for the shared dock geometry frame."""

from __future__ import annotations

from itertools import pairwise
from types import SimpleNamespace

import pytest

from docking.core.layout import content_bounds
from docking.core.position import Position
from docking.core.theme import Theme
from docking.platform.model import DockItem
from docking.ui.autohide import HideState
from docking.ui.geometry import Rect, build_geometry_frame


def _config(pos: Position = Position.BOTTOM) -> SimpleNamespace:
    return SimpleNamespace(
        pos=pos,
        icon_size=48,
        zoom_percent=1.5,
        zoom_enabled=True,
        additional_distance_from_edge=0,
    )


def _theme(distance_from_edge: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        distance_from_edge=distance_from_edge,
        item_padding=8,
        horizontal_padding=12,
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
            horizontal_padding=12,
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

        assert all(a > b for a, b in pairwise(left_scales))
        assert all(scale > 1.0 for scale in left_scales)
        assert left_scales[-1] > 1.0

        assert all(a > b for a, b in pairwise(right_scales))
        assert all(scale > 1.0 for scale in right_scales)


class TestShelfPosition:
    """Verify background_rect shelf position is consistent across hide offsets."""

    def _items(self):
        return [
            DockItem(desktop_id="a.desktop"),
            DockItem(desktop_id="b.desktop"),
            DockItem(desktop_id="c.desktop"),
        ]

    def test_shelf_y_moves_smoothly_with_hide_offset_bottom(self):
        # Given - distance_from_edge > 0 to trigger the drift that was fixed
        items = self._items()
        prev_y = None
        for hide_pct in range(11):
            hide = hide_pct / 10.0
            frame = build_geometry_frame(
                items=items,
                config=_config(Position.BOTTOM),
                theme=_theme(distance_from_edge=6),
                window_w=1920,
                window_h=1080,
                cursor_main=-1.0,
                autohide_state=None,
                hide_offset=hide,
            )
            y = frame.background_rect.y
            # Shelf should move monotonically downward as hide_offset increases
            if prev_y is not None:
                assert y >= prev_y, (
                    f"Shelf y went backwards at hide={hide}: {y} < {prev_y}"
                )
            prev_y = y

    def test_shelf_x_moves_smoothly_with_hide_offset_left(self):
        items = self._items()
        prev_x = None
        for hide_pct in range(11):
            hide = hide_pct / 10.0
            frame = build_geometry_frame(
                items=items,
                config=_config(Position.LEFT),
                theme=_theme(distance_from_edge=6),
                window_w=1920,
                window_h=1080,
                cursor_main=-1.0,
                autohide_state=None,
                hide_offset=hide,
            )
            x = frame.background_rect.x
            # Shelf should move monotonically leftward as hide_offset increases
            if prev_x is not None:
                assert x <= prev_x, f"Shelf x went right at hide={hide}: {x} > {prev_x}"
            prev_x = x

    def test_shelf_fully_hidden_at_offset_1(self):
        # Given
        items = self._items()
        gap = 6
        frame = build_geometry_frame(
            items=items,
            config=_config(Position.BOTTOM),
            theme=_theme(distance_from_edge=gap),
            window_w=1920,
            window_h=1080,
            cursor_main=-1.0,
            autohide_state=None,
            hide_offset=1.0,
        )
        # Then - shelf should be at or beyond the window bottom edge
        assert frame.background_rect.y >= 1080 - gap

    def test_shelf_at_rest_with_distance_from_edge(self):
        # Given
        items = self._items()
        gap = 10
        frame = build_geometry_frame(
            items=items,
            config=_config(Position.BOTTOM),
            theme=_theme(distance_from_edge=gap),
            window_w=1920,
            window_h=1080,
            cursor_main=-1.0,
            autohide_state=None,
            hide_offset=0.0,
        )
        shelf_h = frame.background_rect.h
        shelf_bottom = frame.background_rect.y + shelf_h
        # Shelf bottom should sit above the distance_from_edge gap
        assert shelf_bottom <= 1080 - gap + 1  # +1 for rounding

    def test_shelf_fields_match_background_rect_horizontal(self):
        items = self._items()
        frame = build_geometry_frame(
            items=items,
            config=_config(Position.BOTTOM),
            theme=_theme(distance_from_edge=6),
            window_w=1920,
            window_h=1080,
            cursor_main=-1.0,
            autohide_state=None,
            hide_offset=0.3,
        )
        assert frame.shelf_main_pos == float(frame.background_rect.x)
        assert frame.shelf_main_extent == float(frame.background_rect.w)
        assert frame.shelf_cross_pos == float(frame.background_rect.y)
        assert frame.shelf_cross_extent == float(frame.background_rect.h)

    def test_shelf_fields_match_background_rect_vertical(self):
        items = self._items()
        frame = build_geometry_frame(
            items=items,
            config=_config(Position.LEFT),
            theme=_theme(distance_from_edge=6),
            window_w=1920,
            window_h=1080,
            cursor_main=-1.0,
            autohide_state=None,
            hide_offset=0.3,
        )
        # For vertical docks, main axis is Y, cross is X
        assert frame.shelf_main_pos == float(frame.background_rect.y)
        assert frame.shelf_main_extent == float(frame.background_rect.h)
        assert frame.shelf_cross_pos == float(frame.background_rect.x)
        assert frame.shelf_cross_extent == float(frame.background_rect.w)


_ALL_THEMES = [
    "default",
    "onyx",
    "slate",
    "glass",
    "paper",
    "candy",
    "transparent",
    "olive",
    "ember",
    "nord",
    "gruvbox",
    "solarized",
]


class TestShelfHidesCompletely:
    """Shelf background must be fully off-screen when hide_offset=1.0."""

    @pytest.mark.parametrize("theme_name", _ALL_THEMES)
    def test_shelf_hidden_for_bottom(self, theme_name):
        icon_size = 48
        theme = Theme.load(theme_name, icon_size)
        zoom = 1.5
        gap = max(0, int(theme.distance_from_edge))
        bounce = int(icon_size * theme.urgent_bounce_height)
        cross = int(
            icon_size * zoom + theme.top_padding + theme.bottom_padding + bounce
        )
        window_h = cross + gap
        items = [DockItem(desktop_id="firefox.desktop")]

        frame = build_geometry_frame(
            items=items,
            config=SimpleNamespace(
                pos=Position.BOTTOM,
                icon_size=icon_size,
                zoom_percent=zoom,
                zoom_enabled=True,
                additional_distance_from_edge=0,
            ),
            theme=theme,
            window_w=1920,
            window_h=window_h,
            cursor_main=-1.0,
            autohide_state=HideState.HIDDEN,
            hide_offset=1.0,
        )

        assert frame.background_rect.y >= window_h, (
            f"{theme_name}: shelf_y={frame.background_rect.y} but window_h={window_h}"
        )
