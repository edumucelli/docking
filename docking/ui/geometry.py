"""Shared dock geometry for input, hover, and popup anchoring.

This module centralizes the runtime geometry that the dock needs while a frame
is active: item rectangles, current cursor region, and popup anchor positions.
It intentionally stays free of GTK/GDK objects so the math is easy to test.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from docking.core.position import Position, is_horizontal
from docking.core.zoom import (
    NO_CURSOR_SENTINEL,
    LayoutItem,
    compute_layout,
    content_bounds,
)
from docking.ui.autohide import HideState

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.core.theme import Theme


TRIGGER_PX = 2
TRIGGER_PX_TOP = 8


class Rect(NamedTuple):
    """Half-open rectangle in window-local coordinates."""

    x: int
    y: int
    w: int
    h: int

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.w and self.y <= y < self.y + self.h

    def union(self, other: "Rect") -> "Rect":
        if self.w <= 0 or self.h <= 0:
            return other
        if other.w <= 0 or other.h <= 0:
            return self
        left = min(self.x, other.x)
        top = min(self.y, other.y)
        right = max(self.x + self.w, other.x + other.w)
        bottom = max(self.y + self.h, other.y + other.h)
        return Rect(left, top, right - left, bottom - top)

    def intersection(self, other: "Rect") -> "Rect | None":
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.x + self.w, other.x + other.w)
        bottom = min(self.y + self.h, other.y + other.h)
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right - left, bottom - top)


@dataclass(frozen=True)
class ItemGeometry:
    """Geometry for a single item in the current dock frame."""

    item: "DockItem"
    layout_item: LayoutItem
    draw_rect: Rect
    hover_rect: Rect
    background_rect: Rect
    anchor_x: float
    anchor_y: float
    scaled_size: float
    main_pos: float


@dataclass(frozen=True)
class DockGeometryFrame:
    """One authoritative dock geometry snapshot."""

    window_rect: Rect
    static_dock_rect: Rect
    cursor_rect: Rect
    background_rect: Rect
    layout: tuple[LayoutItem, ...]
    item_geometries: tuple[ItemGeometry, ...]
    local_cursor_main: float
    zoomed_main_offset: float
    cross_size: float

    def item_at_point(self, x: float, y: float) -> "DockItem | None":
        if not self.cursor_rect.contains(x=x, y=y):
            return None
        for item_geometry in self.item_geometries:
            if item_geometry.hover_rect.contains(x=x, y=y):
                return item_geometry.item
        return None

    def item_index_at_point(self, x: float, y: float) -> int:
        if not self.cursor_rect.contains(x=x, y=y):
            return -1
        for index, item_geometry in enumerate(self.item_geometries):
            if item_geometry.hover_rect.contains(x=x, y=y):
                return index
        return -1

    def geometry_for_item(self, item: "DockItem") -> ItemGeometry | None:
        for item_geometry in self.item_geometries:
            if item_geometry.item is item:
                return item_geometry
        return None

    def insertion_index_for_main(self, main_coord: float, pos: Position) -> int:
        for index, item_geometry in enumerate(self.item_geometries):
            if _item_main_center(item_geometry=item_geometry, pos=pos) > main_coord:
                return index
        return len(self.item_geometries)


def compute_input_rect(
    pos: Position,
    window_w: int,
    window_h: int,
    content_offset: int,
    content_w: int,
    content_cross: int,
    autohide_state: HideState | None,
    distance_from_edge: int = 0,
) -> Rect:
    """Return the dock input rectangle for the current state."""
    if autohide_state == HideState.HIDDEN:
        trigger = TRIGGER_PX_TOP if pos == Position.TOP else TRIGGER_PX
        cross = trigger + distance_from_edge
    else:
        cross = max(content_cross, 1) + distance_from_edge
    main = max(content_w, 1)

    if pos == Position.BOTTOM:
        return Rect(content_offset, window_h - cross, main, cross)
    if pos == Position.TOP:
        return Rect(content_offset, 0, main, cross)
    if pos == Position.LEFT:
        return Rect(0, content_offset, cross, main)
    return Rect(window_w - cross, content_offset, cross, main)


def map_icon_position(
    pos: Position,
    main_pos: float,
    cross_size: float,
    edge_padding: float,
    scaled_size: float,
    hide_cross: float = 0.0,
    bounce: float = 0.0,
) -> tuple[float, float]:
    """Map main-axis item position to the icon draw origin."""
    cross_rest = cross_size - edge_padding - scaled_size
    if pos == Position.BOTTOM:
        return main_pos, cross_rest + hide_cross - bounce
    if pos == Position.TOP:
        return main_pos, edge_padding - hide_cross + bounce
    if pos == Position.LEFT:
        return edge_padding - hide_cross + bounce, main_pos
    return cross_rest + hide_cross - bounce, main_pos


def build_geometry_frame(
    *,
    items: list["DockItem"],
    config: "Config",
    theme: "Theme",
    window_w: int,
    window_h: int,
    cursor_main: float,
    autohide_state: HideState | None,
    zoom_progress: float = 1.0,
    hide_offset: float = 0.0,
    drop_insert_index: int = -1,
) -> DockGeometryFrame:
    """Build one shared dock geometry snapshot from current runtime state."""
    pos = config.pos
    horizontal = is_horizontal(pos=pos)
    main_size = window_w if horizontal else window_h
    gap = max(0, int(theme.distance_from_edge))
    cross_size = (window_h if horizontal else window_w) - gap

    local_cursor_main = _local_cursor_main(
        items=items,
        config=config,
        theme=theme,
        main_size=main_size,
        cursor_main=cursor_main,
    )
    layout = tuple(
        compute_layout(
            items,
            config,
            local_cursor_main,
            item_padding=theme.item_padding,
            h_padding=theme.h_padding,
            zoom_progress=zoom_progress,
        )
    )
    left_edge, right_edge = content_bounds(
        layout=list(layout),
        icon_size=config.icon_size,
        h_padding=theme.h_padding,
        item_padding=theme.item_padding,
    )
    zoomed_w = right_edge - left_edge
    dock_main_offset = (main_size - zoomed_w) / 2
    zoomed_main_offset = dock_main_offset - left_edge
    content_cross = int(config.icon_size + theme.bottom_padding)
    static_dock_rect = compute_input_rect(
        pos=pos,
        window_w=window_w,
        window_h=window_h,
        content_offset=int(dock_main_offset),
        content_w=int(zoomed_w),
        content_cross=content_cross,
        autohide_state=(
            None if autohide_state != HideState.HIDDEN else HideState.VISIBLE
        ),
        distance_from_edge=max(0, int(theme.distance_from_edge)),
    )

    drop_gap = config.icon_size + theme.item_padding if drop_insert_index >= 0 else 0.0
    item_geometries, background_rect = _build_item_geometries(
        items=items,
        layout=layout,
        config=config,
        theme=theme,
        pos=pos,
        static_dock_rect=static_dock_rect,
        zoomed_main_offset=zoomed_main_offset,
        cross_size=cross_size,
        hide_offset=hide_offset,
        drop_gap=drop_gap,
    )

    if autohide_state == HideState.HIDDEN:
        cursor_rect = compute_input_rect(
            pos=pos,
            window_w=window_w,
            window_h=window_h,
            content_offset=int(dock_main_offset),
            content_w=int(zoomed_w),
            content_cross=content_cross,
            autohide_state=HideState.HIDDEN,
            distance_from_edge=max(0, int(theme.distance_from_edge)),
        )
    else:
        cursor_rect = static_dock_rect
        for item_geometry in item_geometries:
            cursor_rect = cursor_rect.union(item_geometry.hover_rect)

    return DockGeometryFrame(
        window_rect=Rect(0, 0, window_w, window_h),
        static_dock_rect=static_dock_rect,
        cursor_rect=cursor_rect,
        background_rect=background_rect,
        layout=layout,
        item_geometries=item_geometries,
        local_cursor_main=local_cursor_main,
        zoomed_main_offset=zoomed_main_offset,
        cross_size=cross_size,
    )


def _local_cursor_main(
    *,
    items: list["DockItem"],
    config: "Config",
    theme: "Theme",
    main_size: int,
    cursor_main: float,
) -> float:
    if cursor_main < 0:
        return NO_CURSOR_SENTINEL
    pad = theme.h_padding + theme.item_padding / 2
    total_main = sum(item.main_size or config.icon_size for item in items)
    base_w = pad * 2 + total_main + max(0, len(items) - 1) * theme.item_padding
    return cursor_main - (main_size - base_w) / 2


def _build_item_geometries(
    *,
    items: list["DockItem"],
    layout: tuple[LayoutItem, ...],
    config: "Config",
    theme: "Theme",
    pos: Position,
    static_dock_rect: Rect,
    zoomed_main_offset: float,
    cross_size: float,
    hide_offset: float,
    drop_gap: float,
) -> tuple[tuple[ItemGeometry, ...], Rect]:
    partial_geometries: list[
        tuple[DockItem, LayoutItem, Rect, float, float, float, float]
    ] = []
    hide_cross = hide_offset * cross_size

    for item, layout_item in zip(items, layout):
        base_size = layout_item.width or config.icon_size
        scaled_size = base_size * layout_item.scale
        main_pos = layout_item.x + zoomed_main_offset
        draw_x, draw_y = map_icon_position(
            pos=pos,
            main_pos=main_pos,
            cross_size=cross_size,
            edge_padding=theme.bottom_padding,
            scaled_size=scaled_size,
            hide_cross=hide_cross,
        )
        draw_rect = Rect(
            int(math.floor(draw_x)),
            int(math.floor(draw_y)),
            max(1, int(math.ceil(scaled_size))),
            max(1, int(math.ceil(scaled_size))),
        )
        anchor_x, anchor_y = _item_anchor(
            pos=pos, draw_x=draw_x, draw_y=draw_y, scaled_size=scaled_size
        )
        partial_geometries.append(
            (item, layout_item, draw_rect, anchor_x, anchor_y, scaled_size, main_pos)
        )

    background_rect = _compute_background_rect(
        pos=pos,
        draw_rects=[draw_rect for _, _, draw_rect, *_ in partial_geometries],
        static_dock_rect=static_dock_rect,
        theme=theme,
        hide_offset=hide_offset,
        drop_gap=drop_gap,
    )
    hover_rects = _compute_item_hover_rects(
        pos=pos,
        draw_rects=[draw_rect for _, _, draw_rect, *_ in partial_geometries],
        static_dock_rect=static_dock_rect,
        background_rect=background_rect,
        theme=theme,
    )

    item_geometries: list[ItemGeometry] = []
    for (
        item,
        layout_item,
        draw_rect,
        anchor_x,
        anchor_y,
        scaled_size,
        main_pos,
    ), hover_rect in zip(partial_geometries, hover_rects):
        item_geometries.append(
            ItemGeometry(
                item=item,
                layout_item=layout_item,
                draw_rect=draw_rect,
                hover_rect=hover_rect,
                background_rect=hover_rect.intersection(background_rect)
                or Rect(0, 0, 0, 0),
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                scaled_size=scaled_size,
                main_pos=main_pos,
            )
        )

    return tuple(item_geometries), background_rect


def _compute_item_hover_rects(
    *,
    pos: Position,
    draw_rects: list[Rect],
    static_dock_rect: Rect,
    background_rect: Rect,
    theme: "Theme",
) -> list[Rect]:
    if not draw_rects:
        return []

    base_hover_rects = [
        _compute_item_hover_base_rect(
            pos=pos,
            draw_rect=draw_rect,
            background_rect=background_rect,
            theme=theme,
        )
        for draw_rect in draw_rects
    ]
    boundaries = _compute_main_axis_boundaries(
        pos=pos,
        hover_rects=base_hover_rects,
        static_dock_rect=static_dock_rect,
        background_rect=background_rect,
    )
    hover_rects: list[Rect] = []
    if is_horizontal(pos=pos):
        for base_rect, left, right in zip(base_hover_rects, boundaries, boundaries[1:]):
            hover_rects.append(
                Rect(
                    left,
                    base_rect.y,
                    max(1, right - left),
                    base_rect.h,
                )
            )
        return hover_rects

    for base_rect, top, bottom in zip(base_hover_rects, boundaries, boundaries[1:]):
        hover_rects.append(
            Rect(
                base_rect.x,
                top,
                base_rect.w,
                max(1, bottom - top),
            )
        )
    return hover_rects


def _compute_item_hover_base_rect(
    *,
    pos: Position,
    draw_rect: Rect,
    background_rect: Rect,
    theme: "Theme",
) -> Rect:
    item_padding = max(0, int(round(theme.item_padding)))
    top_padding = max(0, int(math.ceil(theme.top_padding)))
    bottom_padding = max(0, int(math.ceil(theme.bottom_padding)))

    if is_horizontal(pos=pos):
        left = int(math.floor(draw_rect.x - item_padding / 2))
        right = int(math.ceil(draw_rect.x + draw_rect.w + item_padding / 2))
        if pos == Position.BOTTOM:
            top = draw_rect.y - top_padding
            bottom = draw_rect.y + draw_rect.h + bottom_padding
        else:
            top = draw_rect.y - bottom_padding
            bottom = draw_rect.y + draw_rect.h + top_padding
    else:
        top = int(math.floor(draw_rect.y - item_padding / 2))
        bottom = int(math.ceil(draw_rect.y + draw_rect.h + item_padding / 2))
        if pos == Position.LEFT:
            left = draw_rect.x - bottom_padding
            right = draw_rect.x + draw_rect.w + top_padding
        else:
            left = draw_rect.x - top_padding
            right = draw_rect.x + draw_rect.w + bottom_padding

    base_rect = Rect(left, top, max(1, right - left), max(1, bottom - top))
    overlap = base_rect.intersection(background_rect)
    if overlap is None:
        return draw_rect
    return overlap.union(draw_rect)


def _compute_main_axis_boundaries(
    *,
    pos: Position,
    hover_rects: list[Rect],
    static_dock_rect: Rect,
    background_rect: Rect,
) -> list[int]:
    if is_horizontal(pos=pos):
        starts = [min(static_dock_rect.x, background_rect.x)]
        centers = [hover_rect.x + hover_rect.w / 2 for hover_rect in hover_rects]
        for left_center, right_center in zip(centers, centers[1:]):
            starts.append(int(round((left_center + right_center) / 2)))
        starts.append(
            max(
                static_dock_rect.x + static_dock_rect.w,
                background_rect.x + background_rect.w,
            )
        )
        return starts

    starts = [min(static_dock_rect.y, background_rect.y)]
    centers = [hover_rect.y + hover_rect.h / 2 for hover_rect in hover_rects]
    for top_center, bottom_center in zip(centers, centers[1:]):
        starts.append(int(round((top_center + bottom_center) / 2)))
    starts.append(
        max(
            static_dock_rect.y + static_dock_rect.h,
            background_rect.y + background_rect.h,
        )
    )
    return starts


def _compute_background_rect(
    *,
    pos: Position,
    draw_rects: list[Rect],
    static_dock_rect: Rect,
    theme: "Theme",
    hide_offset: float,
    drop_gap: float,
) -> Rect:
    gap = max(0, int(theme.distance_from_edge))
    shelf_cross = max(1, int(round(theme.shelf_height)))
    stroke_width = max(0.0, float(theme.stroke_width))
    main_padding = (
        theme.item_padding + 2 * theme.h_padding + 4 * stroke_width + drop_gap
    )

    if draw_rects:
        if is_horizontal(pos=pos):
            first_center = draw_rects[0].x + draw_rects[0].w / 2
            last_center = draw_rects[-1].x + draw_rects[-1].w / 2
        else:
            first_center = draw_rects[0].y + draw_rects[0].h / 2
            last_center = draw_rects[-1].y + draw_rects[-1].h / 2
        first_half = (
            (draw_rects[0].w if is_horizontal(pos=pos) else draw_rects[0].h)
            + main_padding
        ) / 2
        last_half = (
            (draw_rects[-1].w if is_horizontal(pos=pos) else draw_rects[-1].h)
            + main_padding
        ) / 2
        main_start = int(round(first_center - first_half))
        main_end = int(round(last_center + last_half))
    else:
        if is_horizontal(pos=pos):
            main_start = static_dock_rect.x
            main_end = static_dock_rect.x + static_dock_rect.w
        else:
            main_start = static_dock_rect.y
            main_end = static_dock_rect.y + static_dock_rect.h

    if is_horizontal(pos=pos):
        main_start = min(main_start, static_dock_rect.x)
        main_end = max(main_end, static_dock_rect.x + static_dock_rect.w)
    else:
        main_start = min(main_start, static_dock_rect.y)
        main_end = max(main_end, static_dock_rect.y + static_dock_rect.h)

    if pos == Position.BOTTOM:
        shelf_slide = hide_offset * (static_dock_rect.h - gap) + hide_offset * max(
            0, static_dock_rect.h - gap - shelf_cross
        )
        shelf_y = int(
            round(
                static_dock_rect.y
                + static_dock_rect.h
                - gap
                - shelf_cross
                + shelf_slide
            )
        )
        return Rect(main_start, shelf_y, max(1, main_end - main_start), shelf_cross)
    if pos == Position.TOP:
        shelf_slide = hide_offset * (static_dock_rect.h - gap) + hide_offset * max(
            0, static_dock_rect.h - gap - shelf_cross
        )
        shelf_y = int(round(static_dock_rect.y + gap - shelf_slide))
        return Rect(main_start, shelf_y, max(1, main_end - main_start), shelf_cross)
    if pos == Position.LEFT:
        shelf_slide = hide_offset * (static_dock_rect.w - gap) + hide_offset * max(
            0, static_dock_rect.w - gap - shelf_cross
        )
        shelf_x = int(round(static_dock_rect.x + gap - shelf_slide))
        return Rect(shelf_x, main_start, shelf_cross, max(1, main_end - main_start))
    shelf_slide = hide_offset * (static_dock_rect.w - gap) + hide_offset * max(
        0, static_dock_rect.w - gap - shelf_cross
    )
    shelf_x = int(
        round(static_dock_rect.x + static_dock_rect.w - gap - shelf_cross + shelf_slide)
    )
    return Rect(shelf_x, main_start, shelf_cross, max(1, main_end - main_start))


def _item_anchor(
    *, pos: Position, draw_x: float, draw_y: float, scaled_size: float
) -> tuple[float, float]:
    if pos == Position.BOTTOM:
        return draw_x + scaled_size / 2, draw_y
    if pos == Position.TOP:
        return draw_x + scaled_size / 2, draw_y + scaled_size
    if pos == Position.LEFT:
        return draw_x + scaled_size, draw_y + scaled_size / 2
    return draw_x, draw_y + scaled_size / 2


def _item_main_center(*, item_geometry: ItemGeometry, pos: Position) -> float:
    if is_horizontal(pos=pos):
        return item_geometry.draw_rect.x + item_geometry.draw_rect.w / 2
    return item_geometry.draw_rect.y + item_geometry.draw_rect.h / 2
