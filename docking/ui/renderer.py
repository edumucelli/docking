# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Cairo renderer for the dock's visible output and micro-animations.

What this renderer is responsible for

This module answers one question:

    "Given the current dock frame and visual state, what pixels should be drawn?"

It is intentionally view-only. It should not:

- decide hover policy,
- decide autohide policy,
- mutate the model,
- launch applications,
- resolve item targeting.

Those decisions happen elsewhere. The renderer consumes their results.

Why renderer and geometry are separate

The renderer used to be a tempting place to re-derive visual bounds and item
positions. That leads to drift:

- renderer says icon is here,
- hover says icon is slightly elsewhere,
- menus use another targeting model,
- tooltips anchor from yet another guess.

The current model is:

    DockGeometryFrame
      |
      +--> renderer draws from it
      +--> hover/mouse/menu/dnd also consume it

That means the renderer is no longer the owner of item placement. It is the
consumer of an authoritative frame.

Rendering layers

The dock is painted in a specific order because layers overlap semantically:

1. shelf background
2. shelf active-item glow
3. icons / applets / separators
4. running indicators
5. urgent edge glow when hidden

ASCII view:

    +----------------------------------------------+
    | urgent edge glow (only when hidden)          |
    |----------------------------------------------|
    | running indicators                           |
    | icons / applets / separators                 |
    | shelf glow                                   |
    | shelf background                             |
    +----------------------------------------------+

Changing that order changes behavior, not just aesthetics.

One-dimensional layout, two-dimensional drawing

The dock layout is fundamentally one-dimensional:

- bottom/top dock -> items arranged along X
- left/right dock -> items arranged along Y

The geometry/layout system computes positions in that 1D main-axis space.
The renderer then maps those results into actual 2D draw positions.

That design matters because it keeps all of these aligned:

- zoom math,
- hit testing,
- insertion logic,
- icon positions,
- popup anchors.

If the renderer had its own separate 2D placement logic, the dock would drift
again.

Autohide and rendering

Autohide contributes two visual parameters:

- hide_offset
  how far the dock has moved toward the edge

- zoom_progress (consumed by geometry layer, not the renderer)

Visually:

    fully visible
    [ icons centered on shelf ]

    hiding
    [ icons slide toward edge ] + [ zoom influence decays ]

    hidden
    [ dock mostly gone ] + [ optional urgent edge signal ]

The renderer does not decide those values; it simply makes them visible.

Why offscreen composition is required

The dock window is transparent and compositor-managed. Painting directly to the
target surface in several incremental steps can produce transient clear/repaint
artifacts that the compositor catches as flicker.

So the renderer uses this pattern:

    draw entire frame offscreen
       |
       +--> single blit to visible surface

That makes the result appear atomically instead of revealing intermediate
painting stages.

Shelf transform model

Shelf primitives are conceptually authored in one orientation and then
transformed for the others. That avoids maintaining four almost-identical
variants of corner, gradient, and body math.

In other words:

    one shelf language
      +
    position-dependent transform
      =
    all dock edges

Renderer-local animation state

Not all visual state belongs in the model. Some values are purely presentational
and exist only to make transitions look continuous:

- slide offsets during reorder
- smoothed shelf width
- hover lighten fade values
- average icon-color cache for glows

These caches belong here because they are:

- derived from current UI inputs,
- short-lived,
- irrelevant to application/domain state,
- purely about how the dock looks while changing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.applets.separator.state import STYLE_LINE
from docking.core.items import APP_KIND
from docking.core.position import Position, is_horizontal
from docking.core.theme import RGB, RGBA, IndicatorStyle
from docking.log import get_logger
from docking.ui.autohide import HideState
from docking.ui.effects import average_icon_color, easing_bounce
from docking.ui.geometry import DockGeometryFrame, map_icon_position
from docking.ui.overlays import draw_count_badge, draw_progress_bar
from docking.ui.shelf import clip_shelf_background, draw_shelf_background, rounded_rect

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.core.layout import LayoutItem
    from docking.core.theme import Theme
    from docking.platform.model import DockModel


SHELF_SMOOTH_FACTOR = 0.3
SLIDE_MOVE_THRESHOLD = 2.0
SLIDE_DECAY_FACTOR = 0.75
SLIDE_CLEAR_THRESHOLD = 0.5
INDICATOR_SPACING_MULT = 3

HOVER_LIGHTEN_FRAME_MS = 16

log = get_logger("renderer")
SEPARATOR_LINE_WIDTH_PX = 2.0
SEPARATOR_LINE_START_RATIO = 0.1
SEPARATOR_LINE_END_RATIO = 0.9
ACTIVE_GLOW_PADDING_RATIO = 0.15
URGENT_GLOW_INNER_STOP = 0.33
URGENT_GLOW_INNER_ALPHA = 0.66
URGENT_GLOW_OUTER_STOP = 0.66
URGENT_GLOW_OUTER_ALPHA = 0.33


@dataclass(frozen=True)
class _IconSurfaceCacheKey:
    """Identity for a rendered icon source surface.

    The cache key follows the icon object that the item currently exposes plus
    its raster size. Docking replaces `item.icon` when the underlying pixbuf
    changes, so this is enough to invalidate stale source surfaces without
    coupling the cache to GTK-specific pixbuf internals.
    """

    icon_id: int
    width: int
    height: int

    @classmethod
    def from_item(cls, item: DockItem) -> _IconSurfaceCacheKey | None:
        if item.icon is None:
            return None
        return cls(
            icon_id=id(item.icon),
            width=item.icon.get_width(),
            height=item.icon.get_height(),
        )


@dataclass
class _IconSurfaceCacheEntry:
    """Cached Cairo surface produced from an item's pixbuf-like icon."""

    key: _IconSurfaceCacheKey
    surface: cairo.ImageSurface

    def matches(self, *, item: DockItem) -> bool:
        return self.key == _IconSurfaceCacheKey.from_item(item)


@dataclass
class _OffscreenSurfaceCache:
    """Reusable full-frame backing surface for atomic window blits."""

    width: int
    height: int
    surface: cairo.Surface

    def matches(self, *, width: int, height: int) -> bool:
        return self.width == width and self.height == height


@dataclass
class _RendererCache:
    """Renderer-owned reusable artifacts and their lookup metrics.

    This object is the cache boundary for resources derived from render inputs
    and reused across frames: per-item icon surfaces, per-item average icon
    colors, and the size-matched offscreen frame surface.
    """

    icon_surfaces: dict[str, _IconSurfaceCacheEntry]
    icon_colors: dict[str, RGB]
    offscreen_surface: _OffscreenSurfaceCache | None = None

    def cached_icon_surface_for(self, *, item: DockItem) -> cairo.ImageSurface | None:
        """Return a reusable icon surface when the item's cache key still matches."""
        if item.icon is None:
            self.icon_surfaces.pop(item.desktop_id, None)
            return None
        cached = self.icon_surfaces.get(item.desktop_id)
        if cached is not None and cached.matches(item=item):
            return cached.surface
        return None

    def store_icon_surface(
        self,
        *,
        item: DockItem,
        surface: cairo.ImageSurface,
    ) -> cairo.ImageSurface | None:
        """Store a newly built icon surface under the item's current key."""
        key = _IconSurfaceCacheKey.from_item(item)
        if key is None:
            self.icon_surfaces.pop(item.desktop_id, None)
            return None
        self.icon_surfaces[item.desktop_id] = _IconSurfaceCacheEntry(
            key=key,
            surface=surface,
        )
        return surface

    def prune_icon_surfaces(self, *, items: list[DockItem]) -> None:
        """Drop cached per-item artifacts for items no longer in the frame."""
        active_ids = {item.desktop_id for item in items if item.icon is not None}
        for desktop_id in list(self.icon_surfaces):
            if desktop_id not in active_ids:
                self.icon_surfaces.pop(desktop_id, None)
        for desktop_id in list(self.icon_colors):
            if desktop_id not in active_ids:
                self.icon_colors.pop(desktop_id, None)

    def icon_color_for(self, *, item: DockItem) -> RGB:
        """Compute average icon color once and reuse it for glow rendering."""
        color = self.icon_colors.get(item.desktop_id)
        if color is None:
            color = average_icon_color(pixbuf=item.icon)
            self.icon_colors[item.desktop_id] = color
        return color

    def offscreen_surface_for(
        self,
        *,
        cr: cairo.Context,
        width: int,
        height: int,
    ) -> cairo.Surface:
        """Return a size-matched offscreen surface, reusing it across draws."""
        cached = self.offscreen_surface
        if cached is not None and cached.matches(width=width, height=height):
            return cached.surface
        surface = cr.get_target().create_similar(
            cairo.Content.COLOR_ALPHA,
            width,
            height,
        )
        self.offscreen_surface = _OffscreenSurfaceCache(
            width=width,
            height=height,
            surface=surface,
        )
        return surface


def _draw_indicator_dashes(
    cr: cairo.Context,
    cx: float,
    cy: float,
    radius: float,
    spacing: float,
    count: int,
    horizontal: bool,
) -> None:
    """Draw rounded pill-shaped dashes as running indicators."""
    w = radius * 3  # dash length along main axis
    h = radius  # dash thickness
    corner = h / 2
    for j in range(count):
        offset = (j - (count - 1) / 2) * spacing
        if horizontal:
            rounded_rect(cr, cx + offset - w / 2, cy - h / 2, w, h, corner)
        else:
            rounded_rect(cr, cx - h / 2, cy + offset - w / 2, h, w, corner)
        cr.fill()


def _window_count_label(count: int) -> str:
    return "99+" if count > 99 else str(max(1, count))


def _window_count_bar_height(*, base_size: int, radius: float) -> float:
    return max(radius * 3.0, base_size * 0.18)


def _window_count_dot_height(*, count: int, base_size: int, radius: float) -> float:
    if count <= 1:
        return radius * 2.0
    return float(round(max(radius * 4.2, base_size * 0.22)))


def _snap_count_indicator_rect(
    *,
    cx: float,
    cy: float,
    width: float,
    height: float,
) -> tuple[float, float, float, float]:
    width = float(max(1, round(width)))
    height = float(max(1, round(height)))
    return (
        float(round(cx - width / 2.0)),
        float(round(cy - height / 2.0)),
        width,
        height,
    )


def _inset_count_indicator_anchor(
    *,
    cx: float,
    cy: float,
    cross_extent: float,
    radius: float,
    pos: Position,
) -> tuple[float, float]:
    extra_inset = max(0.0, cross_extent / 2.0 - radius)
    if pos == Position.BOTTOM:
        cy -= extra_inset
    elif pos == Position.TOP:
        cy += extra_inset
    elif pos == Position.LEFT:
        cx += extra_inset
    else:  # RIGHT
        cx -= extra_inset
    return cx, cy


def _draw_indicator_count_bar(
    *,
    cr: cairo.Context,
    cx: float,
    cy: float,
    count: int,
    base_size: int,
    radius: float,
) -> None:
    """Draw one running indicator bar, adding a count when there are many windows."""
    label = _window_count_label(count)
    show_label = count > 1
    height = _window_count_bar_height(base_size=base_size, radius=radius)
    min_width = max(radius * 8.0, base_size * 0.38)

    cr.save()
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(max(8.0, base_size * 0.16))
    ext = cr.text_extents(label)
    padding_x = height * 0.45
    width = max(min_width, ext.width + 2.0 * padding_x) if show_label else min_width
    x, y, width, height = _snap_count_indicator_rect(
        cx=cx,
        cy=cy,
        width=width,
        height=height,
    )

    rounded_rect(cr, x, y, width, height, height / 2.0)
    cr.fill()

    if show_label:
        ascent, descent, *_ = cr.font_extents()
        center_x = x + width / 2.0
        center_y = y + height / 2.0
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
        text_x = center_x - (ext.width / 2.0 + ext.x_bearing)
        text_y = center_y + (ascent - descent) / 2.0
        cr.move_to(text_x, text_y)
        cr.show_text(label)

    cr.restore()


def _draw_indicator_count_dots(
    *,
    cr: cairo.Context,
    cx: float,
    cy: float,
    count: int,
    base_size: int,
    radius: float,
) -> None:
    """Draw a compact numeric dot in the running-indicator position."""
    if count <= 1:
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.fill()
        return

    label = _window_count_label(count)
    cr.save()
    cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
    cr.set_font_size(max(8.0, base_size * 0.15))
    ext = cr.text_extents(label)
    height = _window_count_dot_height(count=count, base_size=base_size, radius=radius)
    width = height if count < 10 else max(height, ext.width + height * 0.55)
    x, y, width, height = _snap_count_indicator_rect(
        cx=cx,
        cy=cy,
        width=width,
        height=height,
    )

    rounded_rect(cr, x, y, width, height, height / 2.0)
    cr.fill()

    ascent, descent, *_ = cr.font_extents()
    center_x = x + width / 2.0
    center_y = y + height / 2.0
    cr.set_operator(cairo.OPERATOR_SOURCE)
    cr.set_source_rgba(1.0, 1.0, 1.0, 0.92)
    cr.move_to(
        center_x - (ext.width / 2.0 + ext.x_bearing),
        center_y + (ascent - descent) / 2.0,
    )
    cr.show_text(label)
    cr.restore()


def compute_urgent_glow_opacity(
    elapsed_us: int, glow_time_ms: int, pulse_ms: int
) -> float:
    """Pulsing opacity for urgent glow (pure function, testable).

    Returns 0.0 after glow_time expires. Otherwise oscillates between
    ~0.2 and ~0.95 via sine wave with period = pulse_ms.
    """
    glow_time_us = glow_time_ms * 1000
    if elapsed_us >= glow_time_us or elapsed_us < 0:
        return 0.0
    pulse_us = pulse_ms * 1000
    phase = elapsed_us / pulse_us * 2 * math.pi
    return 0.2 + 0.75 * (math.sin(phase) + 1) / 2


def has_active_urgent_glow(
    *,
    model: DockModel,
    theme: Theme,
    autohide_state: HideState | None,
    now_us: int,
) -> bool:
    """Return True when any urgent item should still be pulsing at the edge."""
    if autohide_state != HideState.HIDDEN:
        return False
    glow_time_us = theme.urgent_glow_time_ms * 1000
    for item in model.visible_items():
        if item.last_urgent > 0 and (now_us - item.last_urgent) < glow_time_us:
            return True
    return False


def _is_separator_item(item: DockItem) -> bool:
    return item.desktop_id.startswith("applet://separator")


def _separator_prefs(item: DockItem, config: Config) -> tuple[str, bool]:
    key = item.desktop_id.removeprefix("applet://")
    prefs = config.applet_prefs.get(key, {})
    style = prefs.get("style", "space")
    invert = bool(prefs.get("invert_color", False))
    return str(style), invert


def _brightness(rgba: tuple[float, float, float, float]) -> float:
    return 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2]


class DockRenderer:
    """Stateful Cairo renderer for dock visuals and micro-animations.

    The class keeps small presentation caches between frames (slide offsets,
    hover fade values, shelf smoothing, icon color cache) so transitions look
    continuous even though each frame is recomputed from current state.
    """

    def __init__(self) -> None:
        self.slide_offsets: dict[str, float] = {}
        self.prev_positions: dict[str, float] = {}
        self.smooth_shelf_w: float = 0.0
        self._hover_lighten: dict[str, float] = {}
        self._cache = _RendererCache(icon_surfaces={}, icon_colors={})

    @staticmethod
    def has_active_urgent_glow(
        *,
        model: DockModel,
        theme: Theme,
        autohide_state: HideState | None,
        now_us: int,
    ) -> bool:
        """Delegate urgent-edge redraw eligibility to the pure helper."""
        return has_active_urgent_glow(
            model=model,
            theme=theme,
            autohide_state=autohide_state,
            now_us=now_us,
        )

    @staticmethod
    def compute_dock_size(
        model: DockModel,
        config: Config,
        theme: Theme,
    ) -> tuple[int, int]:
        """Compute base dock dimensions (no zoom)."""
        items = model.visible_items()
        num_items = len(items)
        icon_size = config.icon_size
        total_main = sum(item.main_size or icon_size for item in items)
        width = int(
            theme.h_padding * 2
            + total_main
            + max(0, num_items - 1) * theme.item_padding
        )
        height = int(icon_size + theme.top_padding + theme.bottom_padding)
        return max(width, 1), max(height, 1)

    def draw(
        self,
        cr: cairo.Context,
        widget: Gtk.DrawingArea,
        frame: DockGeometryFrame,
        config: Config,
        theme: Theme,
        hide_offset: float = 0.0,
        drag_index: int = -1,
        drop_insert_index: int = -1,
        hovered_id: str = "",
        drop_target_id: str = "",
        cursor_main: float = -1.0,
    ) -> None:
        """Main draw entry point -- called on every 'draw' signal.

        The current `DockGeometryFrame` is built by the window/event layer and
        reused here so rendering does not rebuild a conflicting geometry
        snapshot.
        """
        alloc = widget.get_allocation()
        width, height = alloc.width, alloc.height

        # Render to offscreen surface, then blit atomically with SOURCE.
        # With set_double_buffered(False), we draw directly to the X11
        # backing surface. CLEAR+draw leaves a transparent gap between
        # frames that the compositor can catch. Offscreen avoids this:
        # the window surface is only touched once (the SOURCE blit).
        offscreen = self._cache.offscreen_surface_for(
            cr=cr,
            width=width,
            height=height,
        )
        ocr = cairo.Context(offscreen)
        ocr.save()
        ocr.set_operator(cairo.OPERATOR_CLEAR)
        ocr.paint()
        ocr.restore()
        self._draw_content(
            cr=ocr,
            frame=frame,
            config=config,
            theme=theme,
            hide_offset=hide_offset,
            drag_index=drag_index,
            drop_insert_index=drop_insert_index,
            hovered_id=hovered_id,
            drop_target_id=drop_target_id,
            cursor_main=cursor_main,
        )
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(offscreen, 0, 0)
        cr.paint()

    def _draw_content(
        self,
        cr: cairo.Context,
        frame: DockGeometryFrame,
        config: Config,
        theme: Theme,
        hide_offset: float,
        drag_index: int,
        drop_insert_index: int,
        hovered_id: str,
        drop_target_id: str = "",
        cursor_main: float = -1.0,
    ) -> None:
        """Render all dock content to a Cairo context."""
        pos = config.pos
        width = frame.window_rect.w
        height = frame.window_rect.h
        # Offset content away from the screen edge so the gap area
        # (at the edge) stays transparent for the autohide trigger.
        gap = max(0, int(theme.distance_from_edge))
        if gap > 0:
            if pos == Position.TOP:
                cr.translate(0, gap)
            elif pos == Position.LEFT:
                cr.translate(gap, 0)
            # BOTTOM/RIGHT: gap is at high-y/high-x end, content is
            # already drawn from y=0/x=0 so no translate needed.

        items = [item_geometry.item for item_geometry in frame.item_geometries]
        if not items:
            return
        self._cache.prune_icon_surfaces(items=items)

        icon_size = config.icon_size
        layout = [item_geometry.layout_item for item_geometry in frame.item_geometries]
        cross_size = frame.cross_size
        icon_hide = hide_offset

        # Include the drop gap so shelf expands to cover displaced items
        drop_gap = icon_size + theme.item_padding if drop_insert_index >= 0 else 0
        icon_offset = frame.zoomed_main_offset
        previous_ids = set(self.prev_positions)
        current_ids = {item.desktop_id for item in items}
        membership_changed = bool(previous_ids) and previous_ids != current_ids

        # Shelf coordinates from pre-computed geometry frame (no recomputation)
        target_shelf_w = frame.shelf_main_extent
        if (
            self.smooth_shelf_w == 0.0
            or drop_gap > 0
            or hide_offset > 0
            or membership_changed
        ):
            if membership_changed:
                log.debug(
                    "snap shelf width on membership change: old=%s new=%s width=%.2f",
                    sorted(previous_ids),
                    sorted(current_ids),
                    target_shelf_w,
                )
            self.smooth_shelf_w = target_shelf_w
        else:
            self.smooth_shelf_w += (
                target_shelf_w - self.smooth_shelf_w
            ) * SHELF_SMOOTH_FACTOR
        shelf_main_extent = self.smooth_shelf_w
        shelf_main_pos = frame.shelf_main_pos
        bg_height = frame.shelf_cross_extent
        as_bottom_bg_y = frame.shelf_as_bottom_y

        cr.save()
        self._apply_shelf_transform(
            cr=cr,
            pos=pos,
            width=width,
            height=height,
        )
        draw_shelf_background(
            cr=cr,
            x=shelf_main_pos,
            y=as_bottom_bg_y,
            w=shelf_main_extent,
            h=bg_height,
            theme=theme,
        )

        cr.save()
        if clip_shelf_background(
            cr=cr,
            x=shelf_main_pos,
            y=as_bottom_bg_y,
            w=shelf_main_extent,
            h=bg_height,
            theme=theme,
        ):
            # Active glow (drawn in shelf transform space)
            for item, li in zip(items, layout, strict=True):
                if item.is_active:
                    color = self._cache.icon_color_for(item=item)
                    self._draw_active_glow(
                        cr=cr,
                        li=li,
                        icon_size=icon_size,
                        icon_offset=icon_offset,
                        bg_y=as_bottom_bg_y,
                        bg_height=bg_height,
                        shelf_x=shelf_main_pos,
                        shelf_w=shelf_main_extent,
                        color=color,
                        glow_opacity=theme.glow_opacity,
                    )

            # Drop-target glow: green highlight when dragging a file over a launcher
            if drop_target_id:
                for item, li in zip(items, layout, strict=True):
                    if item.desktop_id == drop_target_id:
                        self._draw_active_glow(
                            cr=cr,
                            li=li,
                            icon_size=icon_size,
                            icon_offset=icon_offset,
                            bg_y=as_bottom_bg_y,
                            bg_height=bg_height,
                            shelf_x=shelf_main_pos,
                            shelf_w=shelf_main_extent,
                            color=(0.2, 0.8, 0.3),
                            glow_opacity=theme.glow_opacity,
                        )
                        break
        cr.restore()
        cr.restore()

        # --- Draw icons ---
        self._update_slide_offsets(items=items, layout=layout, icon_offset=icon_offset)

        gap = icon_size + theme.item_padding if drop_insert_index >= 0 else 0
        self._update_hover_lighten(items=items, hovered_id=hovered_id, theme=theme)

        # Hide offset: distance to push content toward the screen edge
        hide_cross = icon_hide * cross_size

        now = GLib.get_monotonic_time()
        for i, (item, li) in enumerate(zip(items, layout, strict=True)):
            if i == drag_index:
                continue
            slide = self.slide_offsets.get(item.desktop_id, 0.0)
            drop_shift = gap if drop_insert_index >= 0 and i >= drop_insert_index else 0
            lighten = self._hover_lighten.get(item.desktop_id, 0.0)

            darken = 0.0
            click_duration_us = theme.click_time_ms * 1000
            if item.last_clicked > 0:
                ct = now - item.last_clicked
                if ct < click_duration_us:
                    darken = math.sin(math.pi * ct / click_duration_us) * 0.5

            # Bounce away from screen edge
            bounce = 0.0
            launch_duration_us = theme.launch_bounce_time_ms * 1000
            if item.last_launched > 0:
                lt = now - item.last_launched
                bounce += (
                    easing_bounce(t=lt, duration=launch_duration_us, n=2)
                    * icon_size
                    * theme.launch_bounce_height
                )
            urgent_duration_us = theme.urgent_bounce_time_ms * 1000
            if item.last_urgent > 0:
                ut = now - item.last_urgent
                bounce += (
                    easing_bounce(t=ut, duration=urgent_duration_us, n=1)
                    * icon_size
                    * theme.urgent_bounce_height
                )

            item_w = li.width or icon_size
            scaled_size = item_w * li.scale
            main_pos = li.x + icon_offset + slide + drop_shift
            if _is_separator_item(item):
                self._draw_separator(
                    cr=cr,
                    item=item,
                    config=config,
                    theme=theme,
                    pos=pos,
                    main_pos=main_pos,
                    cross_size=cross_size,
                    main_size=scaled_size,
                    hide_cross=hide_cross,
                    bounce=bounce,
                )
                continue
            ix, iy = map_icon_position(
                pos=pos,
                main_pos=main_pos,
                cross_size=cross_size,
                edge_padding=theme.bottom_padding,
                scaled_size=scaled_size,
                hide_cross=hide_cross,
                bounce=bounce,
            )
            expected = frame.item_geometries[i].draw_rect
            render_x = math.floor(ix)
            render_y = math.floor(iy)
            if (
                abs(slide) > SLIDE_CLEAR_THRESHOLD
                or drop_shift != 0
                or bounce != 0.0
                or render_x != expected.x
                or render_y != expected.y
            ):
                log.debug(
                    (
                        "render item: id=%s frame=(%d,%d %dx%d) "
                        "rendered=(%d,%d %.1fx%.1f) "
                        "slide=%.2f drop_shift=%.2f bounce=%.2f layout_x=%.2f"
                    ),
                    item.desktop_id,
                    expected.x,
                    expected.y,
                    expected.w,
                    expected.h,
                    render_x,
                    render_y,
                    scaled_size,
                    scaled_size,
                    slide,
                    drop_shift,
                    bounce,
                    li.x,
                )
            self._draw_icon(
                cr=cr,
                item=item,
                config=config,
                li=li,
                base_size=item_w,
                x=ix,
                y=iy,
                lighten=lighten,
                darken=darken,
            )

        # --- Draw indicators ---
        # While dragging, the dragged item's icon is rendered by GTK as a drag
        # image that follows the cursor, while its layout slot keeps advancing
        # as the model reorders. Drawing the dot at the slot drifts visibly
        # ahead of the cursor until the next reorder snap. Pin the dragged
        # item's indicator to the cursor instead so it tracks the drag ghost.
        for i, (item, li) in enumerate(zip(items, layout, strict=True)):
            if not item.is_running:
                continue
            slide = self.slide_offsets.get(item.desktop_id, 0.0)
            drop_shift = gap if drop_insert_index >= 0 and i >= drop_insert_index else 0
            if i == drag_index and cursor_main >= 0:
                # Center the indicator under the cursor by passing a main_pos
                # such that _draw_indicator's "li.x + main_pos + scaled/2"
                # resolves to cursor_main.
                scaled_size = icon_size * li.scale
                main_pos_indicator = cursor_main - li.x - scaled_size / 2
            else:
                main_pos_indicator = icon_offset + slide + drop_shift
            self._draw_indicator(
                cr=cr,
                item=item,
                li=li,
                show_window_count_numbers=config.show_window_count_numbers,
                base_size=icon_size,
                main_pos=main_pos_indicator,
                cross_size=cross_size,
                hide_cross=hide_cross,
                theme=theme,
                pos=pos,
            )

        # --- Draw per-app overlays ---
        for i, (item, li) in enumerate(zip(items, layout, strict=True)):
            if item.kind != APP_KIND:
                continue
            slide = self.slide_offsets.get(item.desktop_id, 0.0)
            drop_shift = gap if drop_insert_index >= 0 and i >= drop_insert_index else 0

            bounce = 0.0
            launch_duration_us = theme.launch_bounce_time_ms * 1000
            if item.last_launched > 0:
                lt = now - item.last_launched
                bounce += (
                    easing_bounce(t=lt, duration=launch_duration_us, n=2)
                    * icon_size
                    * theme.launch_bounce_height
                )
            urgent_duration_us = theme.urgent_bounce_time_ms * 1000
            if item.last_urgent > 0:
                ut = now - item.last_urgent
                bounce += (
                    easing_bounce(t=ut, duration=urgent_duration_us, n=1)
                    * icon_size
                    * theme.urgent_bounce_height
                )

            item_w = li.width or icon_size
            scaled_size = item_w * li.scale
            main_pos = li.x + icon_offset + slide + drop_shift
            ix, iy = map_icon_position(
                pos=pos,
                main_pos=main_pos,
                cross_size=cross_size,
                edge_padding=theme.bottom_padding,
                scaled_size=scaled_size,
                hide_cross=hide_cross,
                bounce=bounce,
            )

            if item.badge_visible and item.badge_count > 0:
                self._draw_badge(
                    cr=cr,
                    x=ix,
                    y=iy,
                    size=scaled_size,
                    badge_count=item.badge_count,
                )
            if item.progress_visible:
                self._draw_progress(
                    cr=cr,
                    x=ix,
                    y=iy,
                    size=scaled_size,
                    progress=item.progress,
                    color=theme.active_indicator_color,
                )

        # --- Urgent glow at screen edge (only when fully hidden) ---
        if hide_offset >= 1.0:
            for item, li in zip(items, layout, strict=True):
                if item.last_urgent > 0:
                    elapsed = now - item.last_urgent
                    opacity = compute_urgent_glow_opacity(
                        elapsed_us=elapsed,
                        glow_time_ms=theme.urgent_glow_time_ms,
                        pulse_ms=theme.urgent_glow_pulse_ms,
                    )
                    if opacity > 0:
                        color = self._cache.icon_color_for(item=item)
                        self._draw_urgent_glow(
                            cr=cr,
                            li=li,
                            icon_size=icon_size,
                            icon_offset=icon_offset,
                            cross_size=cross_size,
                            pos=pos,
                            theme=theme,
                            color=color,
                            opacity=opacity,
                        )

    @staticmethod
    def _apply_shelf_transform(
        cr: cairo.Context,
        pos: Position,
        width: int,
        height: int,
    ) -> None:
        """Apply Cairo transform so shelf drawing code always works as-if-bottom.

        The shelf code draws a horizontal bar at a given y, with rounded
        top corners and square bottom. After transform:
        - BOTTOM: no change
        - TOP: vertical flip (square edge at screen top)
        - LEFT: rotate so horizontal becomes vertical, square edge at left
        - RIGHT: rotate so horizontal becomes vertical, square edge at right
        """
        if pos == Position.TOP:
            cr.translate(0, height)
            cr.scale(1, -1)
        elif pos == Position.LEFT:
            cr.translate(width, 0)
            cr.rotate(math.pi / 2)
        elif pos == Position.RIGHT:
            cr.rotate(-math.pi / 2)
            cr.translate(-height, 0)
        # BOTTOM: identity -- no transform needed

    def _update_hover_lighten(
        self, items: list[DockItem], hovered_id: str, theme: Theme
    ) -> None:
        """Update per-icon lighten values for hover highlight effect."""
        fade_frames = max(1, theme.active_time_ms // HOVER_LIGHTEN_FRAME_MS)
        hover_max = theme.hover_lighten
        step = hover_max / fade_frames
        active_ids = {item.desktop_id for item in items}

        for item in items:
            did = item.desktop_id
            current = self._hover_lighten.get(did, 0.0)
            if did == hovered_id:
                self._hover_lighten[did] = min(current + step, hover_max)
            elif current > 0:
                new_val = max(current - step, 0.0)
                if new_val > 0:
                    self._hover_lighten[did] = new_val
                else:
                    self._hover_lighten.pop(did, None)

        for did in list(self._hover_lighten):
            if did not in active_ids:
                del self._hover_lighten[did]

    def _update_slide_offsets(
        self, items: list[DockItem], layout: list[LayoutItem], icon_offset: float
    ) -> None:
        """Detect items that changed position and set slide animation offsets."""
        new_positions: dict[str, float] = {}
        for item, li in zip(items, layout, strict=True):
            new_positions[item.desktop_id] = li.x + icon_offset

        previous_ids = set(self.prev_positions)
        new_ids = set(new_positions)
        if previous_ids and previous_ids != new_ids:
            log.debug(
                "reset slide offsets on membership change: old=%s new=%s",
                sorted(previous_ids),
                sorted(new_ids),
            )
            self.slide_offsets.clear()
            self.prev_positions = new_positions
            return

        for desktop_id, new_x in new_positions.items():
            old_x = self.prev_positions.get(desktop_id)
            if old_x is not None and abs(old_x - new_x) > SLIDE_MOVE_THRESHOLD:
                current_slide = self.slide_offsets.get(desktop_id, 0.0)
                self.slide_offsets[desktop_id] = current_slide + (old_x - new_x)
                log.debug(
                    "slide offset updated: id=%s old=%.2f new=%.2f offset=%.2f",
                    desktop_id,
                    old_x,
                    new_x,
                    self.slide_offsets[desktop_id],
                )

        decay = SLIDE_DECAY_FACTOR
        dead = []
        for desktop_id in self.slide_offsets:
            self.slide_offsets[desktop_id] *= decay
            if abs(self.slide_offsets[desktop_id]) < SLIDE_CLEAR_THRESHOLD:
                dead.append(desktop_id)
        for d in dead:
            del self.slide_offsets[d]

        if self.slide_offsets:
            summary = ", ".join(
                f"{desktop_id}={offset:.2f}"
                for desktop_id, offset in sorted(self.slide_offsets.items())
            )
            log.debug("active slide offsets: %s", summary)

        self.prev_positions = new_positions

    def _draw_icon(
        self,
        cr: cairo.Context,
        item: DockItem,
        config: Config,
        li: LayoutItem,
        base_size: int,
        x: float,
        y: float,
        lighten: float = 0.0,
        darken: float = 0.0,
    ) -> None:
        """Draw a single dock icon at (x, y) with hover/click effects."""
        # The common path is "same icon object as last frame". Reuse the cached
        # source surface and rebuild only when the item presents a new icon key.
        source_surface = self._icon_surface_for_item(item=item)
        if source_surface is None:
            return

        scaled_size = base_size * li.scale
        icon_width = source_surface.get_width()
        icon_height = source_surface.get_height()

        if lighten <= 0 and darken <= 0:
            # Idle icons do not need a temporary effect surface. Paint the
            # cached source surface directly and reserve the extra allocation
            # and copy work for frames that actually apply hover/click effects.
            cr.save()
            cr.translate(x, y)
            cr.scale(scaled_size / icon_width, scaled_size / icon_height)
            cr.set_source_surface(source_surface, 0, 0)
            cr.paint()
            cr.restore()
            return

        icon_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, icon_width, icon_height)
        icon_cr = cairo.Context(icon_surface)
        icon_cr.set_source_surface(source_surface, 0, 0)
        icon_cr.paint()

        if lighten > 0:
            icon_cr.set_operator(cairo.OPERATOR_ADD)
            icon_cr.paint_with_alpha(lighten)

        if darken > 0:
            icon_cr.set_operator(cairo.OPERATOR_ATOP)
            icon_cr.set_source_rgba(0, 0, 0, darken)
            icon_cr.paint()

        cr.save()
        cr.translate(x, y)
        cr.scale(scaled_size / icon_width, scaled_size / icon_height)
        cr.set_source_surface(icon_surface, 0, 0)
        cr.paint()
        cr.restore()

    def _draw_separator(
        self,
        cr: cairo.Context,
        item: DockItem,
        config: Config,
        theme: Theme,
        pos: Position,
        main_pos: float,
        cross_size: float,
        main_size: float,
        hide_cross: float = 0.0,
        bounce: float = 0.0,
    ) -> None:
        style, invert_color = _separator_prefs(item=item, config=config)
        if style != STYLE_LINE:
            return

        render_main = max(main_size, 1.0)
        render_cross = float(config.icon_size)
        edge_padding = theme.bottom_padding
        cross_rest = cross_size - edge_padding - render_cross

        if pos == Position.BOTTOM:
            x = main_pos
            y = cross_rest + hide_cross - bounce
        elif pos == Position.TOP:
            x = main_pos
            y = edge_padding - hide_cross + bounce
        elif pos == Position.LEFT:
            x = edge_padding - hide_cross + bounce
            y = main_pos
        else:
            x = cross_rest + hide_cross - bounce
            y = main_pos

        brightness = _brightness(theme.fill_start)
        use_dark = brightness > 0.5
        if invert_color:
            use_dark = not use_dark
        color = (0.0, 0.0, 0.0, 0.4) if use_dark else (1.0, 1.0, 1.0, 0.4)

        cr.save()
        cr.set_source_rgba(*color)
        cr.set_line_width(SEPARATOR_LINE_WIDTH_PX)

        if is_horizontal(pos=pos):
            line_x = x + render_main / 2.0
            cr.move_to(line_x, y + render_cross * SEPARATOR_LINE_START_RATIO)
            cr.line_to(line_x, y + render_cross * SEPARATOR_LINE_END_RATIO)
        else:
            line_y = y + render_main / 2.0
            cr.move_to(x + render_cross * SEPARATOR_LINE_START_RATIO, line_y)
            cr.line_to(x + render_cross * SEPARATOR_LINE_END_RATIO, line_y)

        cr.stroke()
        cr.restore()

    def _icon_surface_for_item(self, item: DockItem) -> cairo.ImageSurface | None:
        cached = self._cache.cached_icon_surface_for(item=item)
        if cached is not None:
            return cached
        surface = self._pixbuf_surface(pixbuf=item.icon)
        if surface is None:
            self._cache.icon_surfaces.pop(item.desktop_id, None)
            return None
        return self._cache.store_icon_surface(item=item, surface=surface)

    @staticmethod
    def _pixbuf_surface(pixbuf: object) -> cairo.ImageSurface | None:
        if pixbuf is None:
            return None
        width = pixbuf.get_width()
        height = pixbuf.get_height()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        cr = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        return surface

    @staticmethod
    def _draw_active_glow(
        cr: cairo.Context,
        li: LayoutItem,
        icon_size: int,
        icon_offset: float,
        bg_y: float,
        bg_height: float,
        shelf_x: float,
        shelf_w: float,
        color: RGB,
        glow_opacity: float = 0.6,
    ) -> None:
        """Draw a color-matched glow on the shelf behind the active icon.

        Drawn in the shelf's transform space (always as-if-bottom).
        """
        glow_x = li.x + icon_offset
        glow_width = icon_size * li.scale
        glow_pad = glow_width * ACTIVE_GLOW_PADDING_RATIO

        glow_red, glow_green, glow_blue = color
        gradient = cairo.LinearGradient(0, bg_y, 0, bg_y + bg_height)
        gradient.add_color_stop_rgba(0, glow_red, glow_green, glow_blue, 0.0)
        gradient.add_color_stop_rgba(1, glow_red, glow_green, glow_blue, glow_opacity)

        left = max(glow_x - glow_pad, shelf_x)
        right = min(glow_x + glow_width + glow_pad, shelf_x + shelf_w)
        if right > left:
            cr.rectangle(left, bg_y, right - left, bg_height)
            cr.set_source(gradient)
            cr.fill()

    @staticmethod
    def _draw_urgent_glow(
        cr: cairo.Context,
        li: LayoutItem,
        icon_size: int,
        icon_offset: float,
        cross_size: float,
        pos: Position,
        theme: Theme,
        color: RGB,
        opacity: float,
    ) -> None:
        """Draw a pulsing radial glow at the screen edge for an urgent item.

        Positioned at the screen edge (where the dock hides into), centered
        on the item's main-axis position. Half the glow extends off-screen.
        Radial gradient: white center -> colored -> transparent.
        """
        glow_r = icon_size * theme.urgent_glow_size
        scaled_size = icon_size * li.scale
        main_center = li.x + icon_offset + scaled_size / 2
        r, g, b = color

        # Position glow center at screen edge, centered on item
        if pos == Position.BOTTOM:
            gx, gy = main_center, cross_size
        elif pos == Position.TOP:
            gx, gy = main_center, 0.0
        elif pos == Position.LEFT:
            gx, gy = 0.0, main_center
        else:  # RIGHT
            gx, gy = cross_size, main_center

        grad = cairo.RadialGradient(gx, gy, 0, gx, gy, glow_r)
        grad.add_color_stop_rgba(0, 1, 1, 1, 1.0)
        grad.add_color_stop_rgba(
            URGENT_GLOW_INNER_STOP,
            r,
            g,
            b,
            URGENT_GLOW_INNER_ALPHA,
        )
        grad.add_color_stop_rgba(
            URGENT_GLOW_OUTER_STOP,
            r,
            g,
            b,
            URGENT_GLOW_OUTER_ALPHA,
        )
        grad.add_color_stop_rgba(1.0, r, g, b, 0.0)

        cr.arc(gx, gy, glow_r, 0, 2 * math.pi)
        cr.set_source(grad)
        cr.paint_with_alpha(opacity)
        cr.new_path()

    @staticmethod
    def _draw_indicator(
        cr: cairo.Context,
        item: DockItem,
        li: LayoutItem,
        show_window_count_numbers: bool,
        base_size: int,
        main_pos: float,
        cross_size: float,
        hide_cross: float,
        theme: Theme,
        pos: Position,
    ) -> None:
        """Draw running indicator(s) near the screen edge."""
        scaled_size = base_size * li.scale
        main_center = li.x + main_pos + scaled_size / 2
        edge_padding = theme.bottom_padding

        color = (
            theme.active_indicator_color if item.is_active else theme.indicator_color
        )
        cr.set_source_rgba(*color)

        count = min(item.instance_count, theme.max_indicator_dots)
        radius = theme.indicator_radius
        spacing = radius * INDICATOR_SPACING_MULT

        # Resolve anchor point and orientation once for all positions
        horizontal = pos in (Position.BOTTOM, Position.TOP)
        if pos == Position.BOTTOM:
            cx, cy = main_center, cross_size - edge_padding / 2 + hide_cross
        elif pos == Position.TOP:
            cx, cy = main_center, edge_padding / 2 - hide_cross
        elif pos == Position.LEFT:
            cx, cy = edge_padding / 2 - hide_cross, main_center
        else:  # RIGHT
            cx, cy = cross_size - edge_padding / 2 + hide_cross, main_center

        if show_window_count_numbers and theme.indicator_style == IndicatorStyle.DASHES:
            count_cx, count_cy = _inset_count_indicator_anchor(
                cx=cx,
                cy=cy,
                cross_extent=_window_count_bar_height(
                    base_size=base_size,
                    radius=radius,
                ),
                radius=radius,
                pos=pos,
            )
            _draw_indicator_count_bar(
                cr=cr,
                cx=count_cx,
                cy=count_cy,
                count=item.instance_count,
                base_size=base_size,
                radius=radius,
            )
        elif show_window_count_numbers:
            count_cx, count_cy = _inset_count_indicator_anchor(
                cx=cx,
                cy=cy,
                cross_extent=_window_count_dot_height(
                    count=item.instance_count,
                    base_size=base_size,
                    radius=radius,
                ),
                radius=radius,
                pos=pos,
            )
            _draw_indicator_count_dots(
                cr=cr,
                cx=count_cx,
                cy=count_cy,
                count=item.instance_count,
                base_size=base_size,
                radius=radius,
            )
        elif theme.indicator_style == IndicatorStyle.DASHES:
            _draw_indicator_dashes(cr, cx, cy, radius, spacing, count, horizontal)
        else:  # DOTS
            for j in range(count):
                offset = (j - (count - 1) / 2) * spacing
                if horizontal:
                    cr.arc(cx + offset, cy, radius, 0, 2 * math.pi)
                else:
                    cr.arc(cx, cy + offset, radius, 0, 2 * math.pi)
                cr.fill()

    @staticmethod
    def _draw_badge(
        *,
        cr: cairo.Context,
        x: float,
        y: float,
        size: float,
        badge_count: int,
    ) -> None:
        padding_x = size * 0.10
        badge_height = max(size * 0.34, 14.0)
        cr.save()
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        if badge_count <= 9:
            font_size = size * 0.20
        elif badge_count <= 99:
            font_size = size * 0.16
        else:
            font_size = size * 0.12
        cr.set_font_size(max(7.0, font_size))
        ext = cr.text_extents("99+" if badge_count > 99 else str(badge_count))
        badge_width = max(
            badge_height,
            ext.width + 2 * padding_x,
        )
        badge_x = x + size - badge_width - size * 0.02
        badge_y = y + size * 0.02
        cr.restore()

        draw_count_badge(
            cr=cr,
            x=badge_x,
            y=badge_y,
            width=badge_width,
            height=badge_height,
            badge_count=badge_count,
        )

    @staticmethod
    def _draw_progress(
        *,
        cr: cairo.Context,
        x: float,
        y: float,
        size: float,
        progress: float,
        color: RGB | RGBA,
    ) -> None:
        bar_width = size * 0.72
        bar_height = max(size * 0.10, 4.0)
        bar_x = x + (size - bar_width) / 2
        bar_y = y + size * 0.78
        draw_progress_bar(
            cr=cr,
            x=bar_x,
            y=bar_y,
            width=bar_width,
            height=bar_height,
            progress=progress,
            color=color,
        )
