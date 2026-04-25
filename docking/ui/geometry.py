"""Shared dock geometry used by rendering, hover, input masking, and popups.

Why this module exists

The dock used to compute "where things are" in several different places:

- drawing code decided where the shelf and icons appeared,
- event handlers decided independently what counted as "inside" the dock,
- hover code had its own idea of which icon was active,
- tooltip/preview code rebuilt anchor positions again.

That arrangement always drifts. The visible dock can say "the icon is here"
while the input region says "the icon is slightly over there". The result is
the class of bugs that feel like:

- the pointer leaves an icon and it snaps too early,
- the dock hides on one edge before the other,
- right-clicking the visible shelf opens an item menu instead of the dock menu,
- a popup appears attached to the wrong moving point.

This module is the answer to that problem. It builds one explicit geometry
snapshot for the current dock state and gives every consumer the same numbers.

What this module owns

This module owns geometry only. In concrete terms that means:

- converting runtime inputs into one dock frame,
- describing where the dock background lives inside the GTK window,
- describing where each item draws,
- describing where each item is hovered,
- describing where each item is clickable,
- describing the current dock cursor/input region,
- describing popup anchor coordinates.

This module does not own:

- GTK signal handling,
- autohide policy decisions,
- drag/drop policy,
- tooltip timing,
- preview timing,
- window manager integration.

Those subsystems consume geometry; they do not define it.

Coordinate systems

There are three coordinate ideas that matter here:

1. Window-local coordinates
   Every Rect in this module is in the dock window's local space.

2. Main axis
   The dock is treated as if it always grows along one axis:

       top / bottom dock  -> main axis is X
       left / right dock  -> main axis is Y

   This lets layout, hit testing, insertion logic, and hover logic share the
   same math instead of branching into four nearly-identical copies.

3. Cross axis
   The axis perpendicular to the main axis. This is where "distance from edge",
   shelf thickness, and hidden trigger thickness matter.

The window, background, and item regions

The GTK window is intentionally larger and more stable than the actual active
dock region. The window can stay edge-aligned and avoid resize wobble while the
interactive part shrinks, expands, or animates inside it.

The important rectangles are:

    window_rect
    +-------------------------------------------------------------+
    |                                                             |
    |   static_dock_rect / background_rect                        |
    |   +-----------------------------------------------+         |
    |   | item 0 | item 1 | item 2 | item 3 | item 4   |         |
    |   +-----------------------------------------------+         |
    |                                                             |
    +-------------------------------------------------------------+

The dock does not use one item rectangle for everything. Each item has:

- draw_rect
  The pixels used by the renderer for the icon in this frame.

- hover_rect
  The broader region that should still count as "hovering this item".
  This is intentionally more forgiving than the exact draw rectangle so that
  pointer movement near edges feels smooth.

- hit_rect
  The narrower region used for click/menu targeting.
  This is what allows the state "inside the dock, but not on any item", which
  is required for the dock background menu.

- background_rect
  The portion of the shelf/background conceptually associated with that item.
  This matters when the outer shelf shape and the icon shape are not identical.

One dock-wide region matters just as much:

- cursor_rect
  The region that means "the pointer is on the dock".
  Autohide, effective enter/leave, and input masking treat this as the dock's
  authoritative interactive band.

Why half-open rectangles are mandatory

Containment uses half-open bounds:

    x in [left, right)
    y in [top, bottom)

In code:

    left <= x < right
    top  <= y < bottom

This matters because adjacent regions must meet without overlap and without
gaps. If both neighboring regions treated their right/bottom edge as inclusive,
the same pixel could belong to two items. If both excluded too much, one pixel
strips appear where no item or dock region claims the pointer.

The edge bugs fixed during the geometry refactor were largely caused by this
kind of mismatch.

How one frame is built

The builder follows this sequence:

1. Capture runtime state:
   - items
   - theme/config
   - window size
   - main-axis cursor
   - autohide state
   - hide offset / zoom progress
   - active drag insertion index

2. Compute zoom/layout output from the current main-axis cursor.

3. Derive the dock band and background rectangle.

4. Derive one ItemGeometry per visible item.

5. Derive cursor_rect, which may differ from the fully visible background:
   - when hidden, it collapses to a thin trigger strip,
   - when visible, it expands to cover the active dock band.

6. Publish the result as one DockGeometryFrame.

Why DockGeometryInputs exists

This module separates runtime capture from pure geometry math:

- DockGeometryBuilder
  Lives at the UI boundary and knows how to ask the dock window for live state.

- DockGeometryInputs
  An explicit snapshot of those live inputs.

- build_geometry_frame(...)
  Pure geometry math that can be tested without GTK.

That split prevents geometry code from gradually turning into another
"everything knows about DockWindow" layer.

How the frame is consumed

The same frame, or frames built from the same rules, are used by:

- renderer:
  draw shelf and icons from draw_rect/background_rect

- hover:
  determine hovered item from hover_rect

- menus and button targeting:
  determine item-vs-background hit from hit_rect

- input mask:
  determine what part of the window should intercept pointer input

- tooltips and previews:
  anchor popups to item anchor coordinates

Visually, the relationship is:

    pointer
      |
      v
    cursor_rect ? -------------------- no -> outside dock
      |
      +-- hover_rect match? ---------- yes -> hovered item
      |
      +-- hit_rect match? ------------ yes -> click targets item
      |
      +-- otherwise ------------------ dock background

This distinction is intentional. Hover and click are not the same problem.

What "good" looks like

If this module is doing its job, the rest of the dock code should not need
geometry hacks such as:

- "pad the left edge by a few pixels",
- "if the right-most icon is hovered, special case the exit",
- "this popup should guess where the icon is from event coordinates",
- "rebuild a second hit-test model for menus".

When one of those appears, the right fix is almost always to extend or correct
the geometry frame here, not to patch individual consumers elsewhere.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import pairwise
from typing import TYPE_CHECKING, NamedTuple

from docking.core.layout import (
    NO_CURSOR_SENTINEL,
    LayoutItem,
    compute_layout,
    content_bounds,
)
from docking.core.position import Position, is_horizontal
from docking.ui.autohide import HideState

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.core.theme import Theme
    from docking.ui.dock_window import DockWindow


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

    def union(self, other: Rect) -> Rect:
        if self.w <= 0 or self.h <= 0:
            return other
        if other.w <= 0 or other.h <= 0:
            return self
        left = min(self.x, other.x)
        top = min(self.y, other.y)
        right = max(self.x + self.w, other.x + other.w)
        bottom = max(self.y + self.h, other.y + other.h)
        return Rect(left, top, right - left, bottom - top)

    def intersection(self, other: Rect) -> Rect | None:
        left = max(self.x, other.x)
        top = max(self.y, other.y)
        right = min(self.x + self.w, other.x + other.w)
        bottom = min(self.y + self.h, other.y + other.h)
        if right <= left or bottom <= top:
            return None
        return Rect(left, top, right - left, bottom - top)


def anchor_from_draw_rect(
    *, win_x: int, win_y: int, draw_rect: Rect, position: Position
) -> tuple[int, int]:
    """Translate an item draw rect into the preview/hover anchor point."""
    if position == Position.BOTTOM:
        return int(win_x + draw_rect.x), int(win_y + draw_rect.y)
    if position == Position.TOP:
        return int(win_x + draw_rect.x), int(win_y + draw_rect.y + draw_rect.h)
    if position == Position.LEFT:
        return int(win_x + draw_rect.x + draw_rect.w), int(win_y + draw_rect.y)
    return int(win_x + draw_rect.x), int(win_y + draw_rect.y)


@dataclass(frozen=True)
class ItemGeometry:
    """Geometry for a single item in the current dock frame."""

    item: DockItem
    layout_item: LayoutItem
    draw_rect: Rect
    hover_rect: Rect
    hit_rect: Rect
    background_rect: Rect
    anchor_x: float
    anchor_y: float
    scaled_size: float
    main_pos: float

    def anchor_point(
        self, *, win_x: int, win_y: int, position: Position
    ) -> tuple[int, int]:
        """Absolute preview/hover anchor for this item."""
        return anchor_from_draw_rect(
            win_x=win_x,
            win_y=win_y,
            draw_rect=self.draw_rect,
            position=position,
        )


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
    # Pre-computed shelf drawing coordinates (orientation-independent).
    shelf_main_pos: float = 0.0
    shelf_main_extent: float = 0.0
    shelf_cross_pos: float = 0.0
    shelf_cross_extent: float = 0.0
    # Shelf Y in the renderer's canonical "as-if-bottom" coordinate space.
    # Single source of truth - renderer uses this directly, no recomputation.
    shelf_as_bottom_y: float = 0.0

    def item_at_point(self, x: float, y: float) -> DockItem | None:
        index = self.item_index_at_point(x=x, y=y)
        return self.item_geometries[index].item if index >= 0 else None

    def hover_item_at_point(self, x: float, y: float) -> DockItem | None:
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
            if item_geometry.hit_rect.contains(x=x, y=y):
                return index
        return -1

    def geometry_for_item(self, item: DockItem) -> ItemGeometry | None:
        for item_geometry in self.item_geometries:
            if item_geometry.item is item:
                return item_geometry
        return None

    def insertion_index_for_main(self, main_coord: float, pos: Position) -> int:
        for index, item_geometry in enumerate(self.item_geometries):
            if _item_main_center(item_geometry=item_geometry, pos=pos) > main_coord:
                return index
        return len(self.item_geometries)


@dataclass(frozen=True)
class DockGeometryInputs:
    """Explicit runtime inputs required to build one geometry frame."""

    items: tuple[DockItem, ...]
    config: Config
    theme: Theme
    window_w: int
    window_h: int
    cursor_main: float
    autohide_state: HideState | None
    zoom_progress: float
    hide_offset: float
    drop_insert_index: int = -1


class DockGeometryBuilder:
    """Window-bound adapter that builds geometry frames from live runtime state."""

    def __init__(self, window: DockWindow) -> None:
        self._window = window

    def build_frame(
        self,
        *,
        main_cursor: float | None = None,
        cursor_x: float | None = None,
        cursor_y: float | None = None,
        drop_insert_index: int = -1,
    ) -> DockGeometryFrame:
        inputs = capture_geometry_inputs(
            self._window,
            main_cursor=main_cursor,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            drop_insert_index=drop_insert_index,
        )
        return build_geometry_frame(
            items=list(inputs.items),
            config=inputs.config,
            theme=inputs.theme,
            window_w=inputs.window_w,
            window_h=inputs.window_h,
            cursor_main=inputs.cursor_main,
            autohide_state=inputs.autohide_state,
            zoom_progress=inputs.zoom_progress,
            hide_offset=inputs.hide_offset,
            drop_insert_index=inputs.drop_insert_index,
        )


def current_input_rect(frame: DockGeometryFrame | None) -> Rect | None:
    """Return the current dock input rect from a geometry frame, if any."""
    if frame is None:
        return None
    return frame.cursor_rect


def point_inside_input_rect(
    frame: DockGeometryFrame | None, x: float, y: float
) -> bool:
    """Return True when the given local-window point is inside the input rect."""
    input_rect = current_input_rect(frame)
    if input_rect is None:
        return False
    return input_rect.contains(x=x, y=y)


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
    items: list[DockItem],
    config: Config,
    theme: Theme,
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

    horizontal = is_horizontal(pos=pos)
    shelf_cross_pos = float(background_rect.y if horizontal else background_rect.x)
    shelf_cross_extent = float(background_rect.h if horizontal else background_rect.w)

    # Compute shelf Y in the renderer's canonical "as-if-bottom" space.
    # BOTTOM/RIGHT: screen coords match as-if-bottom, use cross_pos directly.
    # TOP/LEFT: the renderer's transform mirrors, so reverse-map.
    if pos == Position.BOTTOM or pos == Position.RIGHT:
        as_bottom_y = shelf_cross_pos
    elif pos == Position.TOP:
        as_bottom_y = float(window_h) - shelf_cross_pos - shelf_cross_extent
    else:  # LEFT
        as_bottom_y = float(window_w) - shelf_cross_pos - shelf_cross_extent

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
        shelf_main_pos=float(background_rect.x if horizontal else background_rect.y),
        shelf_main_extent=float(background_rect.w if horizontal else background_rect.h),
        shelf_cross_pos=shelf_cross_pos,
        shelf_cross_extent=shelf_cross_extent,
        shelf_as_bottom_y=as_bottom_y,
    )


def capture_geometry_inputs(
    window: DockWindow,
    *,
    main_cursor: float | None = None,
    cursor_x: float | None = None,
    cursor_y: float | None = None,
    drop_insert_index: int = -1,
) -> DockGeometryInputs:
    """Capture explicit geometry inputs from the current dock window state."""
    width, height = window.get_size()
    pos = window.config.pos
    if main_cursor is not None:
        resolved_main_cursor = main_cursor
    elif cursor_x is not None and cursor_y is not None:
        resolved_main_cursor = cursor_x if is_horizontal(pos=pos) else cursor_y
    else:
        resolved_main_cursor = (
            window.cursor_x if is_horizontal(pos=pos) else window.cursor_y
        )

    autohide_state = window.autohide.state if window.autohide.enabled else None
    autohide_zoom = window.autohide.zoom_progress if window.autohide.enabled else 1.0
    zoom_progress = window.zoom_animator.progress * autohide_zoom
    hide_offset = window.autohide.hide_offset if window.autohide.enabled else 0.0
    return DockGeometryInputs(
        items=tuple(window.model.visible_items()),
        config=window.config,
        theme=window.theme,
        window_w=width,
        window_h=height,
        cursor_main=(
            -1.0 if resolved_main_cursor is None else float(resolved_main_cursor)
        ),
        autohide_state=autohide_state,
        zoom_progress=zoom_progress,
        hide_offset=hide_offset,
        drop_insert_index=drop_insert_index,
    )


def _local_cursor_main(
    *,
    items: list[DockItem],
    config: Config,
    theme: Theme,
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
    items: list[DockItem],
    layout: tuple[LayoutItem, ...],
    config: Config,
    theme: Theme,
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

    for item, layout_item in zip(items, layout, strict=True):
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
            math.floor(draw_x),
            math.floor(draw_y),
            max(1, math.ceil(scaled_size)),
            max(1, math.ceil(scaled_size)),
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
    hit_rects = _compute_item_hit_rects(
        pos=pos,
        draw_rects=[draw_rect for _, _, draw_rect, *_ in partial_geometries],
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
    ), hover_rect, hit_rect in zip(
        partial_geometries, hover_rects, hit_rects, strict=True
    ):
        item_geometries.append(
            ItemGeometry(
                item=item,
                layout_item=layout_item,
                draw_rect=draw_rect,
                hover_rect=hover_rect,
                hit_rect=hit_rect,
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
    theme: Theme,
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
        for base_rect, left, right in zip(
            base_hover_rects, boundaries, boundaries[1:], strict=False
        ):
            hover_rects.append(
                Rect(
                    left,
                    base_rect.y,
                    max(1, right - left),
                    base_rect.h,
                )
            )
        return hover_rects

    for base_rect, top, bottom in zip(
        base_hover_rects, boundaries, boundaries[1:], strict=False
    ):
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
    theme: Theme,
) -> Rect:
    item_padding = max(0, round(theme.item_padding))
    top_padding = max(0, math.ceil(theme.top_padding))
    bottom_padding = max(0, math.ceil(theme.bottom_padding))

    if is_horizontal(pos=pos):
        left = math.floor(draw_rect.x - item_padding / 2)
        right = math.ceil(draw_rect.x + draw_rect.w + item_padding / 2)
        if pos == Position.BOTTOM:
            top = draw_rect.y - top_padding
            bottom = draw_rect.y + draw_rect.h + bottom_padding
        else:
            top = draw_rect.y - bottom_padding
            bottom = draw_rect.y + draw_rect.h + top_padding
    else:
        top = math.floor(draw_rect.y - item_padding / 2)
        bottom = math.ceil(draw_rect.y + draw_rect.h + item_padding / 2)
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


def _compute_item_hit_rects(
    *,
    pos: Position,
    draw_rects: list[Rect],
    background_rect: Rect,
    theme: Theme,
) -> list[Rect]:
    return [
        _compute_item_hit_rect(
            pos=pos,
            draw_rect=draw_rect,
            background_rect=background_rect,
            theme=theme,
        )
        for draw_rect in draw_rects
    ]


def _compute_item_hit_rect(
    *,
    pos: Position,
    draw_rect: Rect,
    background_rect: Rect,
    theme: Theme,
) -> Rect:
    main_pad = max(0, round(theme.item_padding / 2))
    if is_horizontal(pos=pos):
        center_x = draw_rect.x + draw_rect.w / 2
        width = draw_rect.w + main_pad
        left = round(center_x - width / 2)
        right = left + max(1, width)
        top = min(draw_rect.y, background_rect.y)
        bottom = max(draw_rect.y + draw_rect.h, background_rect.y + background_rect.h)
        return Rect(left, top, max(1, right - left), max(1, bottom - top))

    center_y = draw_rect.y + draw_rect.h / 2
    height = draw_rect.h + main_pad
    top = round(center_y - height / 2)
    bottom = top + max(1, height)
    left = min(draw_rect.x, background_rect.x)
    right = max(draw_rect.x + draw_rect.w, background_rect.x + background_rect.w)
    return Rect(left, top, max(1, right - left), max(1, bottom - top))


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
        for left_center, right_center in pairwise(centers):
            starts.append(round((left_center + right_center) / 2))
        starts.append(
            max(
                static_dock_rect.x + static_dock_rect.w,
                background_rect.x + background_rect.w,
            )
        )
        return starts

    starts = [min(static_dock_rect.y, background_rect.y)]
    centers = [hover_rect.y + hover_rect.h / 2 for hover_rect in hover_rects]
    for top_center, bottom_center in pairwise(centers):
        starts.append(round((top_center + bottom_center) / 2))
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
    theme: Theme,
    hide_offset: float,
    drop_gap: float,
) -> Rect:
    gap = max(0, int(theme.distance_from_edge))
    shelf_cross = max(1, round(theme.shelf_height))
    stroke_width = max(0.0, float(theme.stroke_width))
    main_padding = theme.item_padding + 2 * theme.h_padding + 4 * stroke_width

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
        main_start = round(first_center - first_half)
        main_end = round(last_center + last_half + drop_gap)
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
        shelf_slide = hide_offset * (shelf_cross + gap)
        shelf_y = round(
            static_dock_rect.y + static_dock_rect.h - gap - shelf_cross + shelf_slide
        )
        return Rect(main_start, shelf_y, max(1, main_end - main_start), shelf_cross)
    if pos == Position.TOP:
        shelf_slide = hide_offset * (shelf_cross + gap)
        shelf_y = round(static_dock_rect.y + gap - shelf_slide)
        return Rect(main_start, shelf_y, max(1, main_end - main_start), shelf_cross)
    if pos == Position.LEFT:
        shelf_slide = hide_offset * (shelf_cross + gap)
        shelf_x = round(static_dock_rect.x + gap - shelf_slide)
        return Rect(shelf_x, main_start, shelf_cross, max(1, main_end - main_start))
    shelf_slide = hide_offset * (shelf_cross + gap)
    shelf_x = round(
        static_dock_rect.x + static_dock_rect.w - gap - shelf_cross + shelf_slide
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
