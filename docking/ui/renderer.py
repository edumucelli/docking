"""Cairo renderer for the dock -- background, icons, indicators, zoom visualization."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from docking.core.position import Position, is_horizontal
from docking.core.theme import RGB
from docking.core.zoom import compute_layout, content_bounds
from docking.ui.effects import average_icon_color, easing_bounce
from docking.ui.shelf import draw_shelf_background

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.core.zoom import LayoutItem
    from docking.platform.model import DockItem, DockModel


SHELF_SMOOTH_FACTOR = 0.3
SLIDE_MOVE_THRESHOLD = 2.0
SLIDE_DECAY_FACTOR = 0.75
SLIDE_CLEAR_THRESHOLD = 0.5
INDICATOR_SPACING_MULT = 3

SLIDE_DURATION_MS = 300
SLIDE_FRAME_MS = 16


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


def map_icon_position(
    pos: Position,
    main_pos: float,
    cross_size: float,
    edge_padding: float,
    scaled_size: float,
    hide_cross: float = 0.0,
    bounce: float = 0.0,
) -> tuple[float, float]:
    """Map 1D main-axis position to (x, y) for a given dock position.

    Returns the top-left corner of the icon in window coordinates.
    """
    cross_rest = cross_size - edge_padding - scaled_size
    if pos == Position.BOTTOM:
        return main_pos, cross_rest + hide_cross - bounce
    elif pos == Position.TOP:
        return main_pos, edge_padding - hide_cross + bounce
    elif pos == Position.LEFT:
        return edge_padding - hide_cross + bounce, main_pos
    else:  # RIGHT
        return cross_rest + hide_cross - bounce, main_pos


class DockRenderer:
    """Dock renderer with per-item slide animation for reordering."""

    def __init__(self) -> None:
        self.slide_offsets: dict[str, float] = {}
        self.prev_positions: dict[str, float] = {}
        self.smooth_shelf_w: float = 0.0
        self._hover_lighten: dict[str, float] = {}
        self._hovered_id: str = ""
        self._icon_colors: dict[str, RGB] = {}

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
        width = int(
            theme.h_padding * 2
            + num_items * icon_size
            + max(0, num_items - 1) * theme.item_padding
        )
        height = int(icon_size + theme.top_padding + theme.bottom_padding)
        return max(width, 1), max(height, 1)

    def draw(
        self,
        cr: cairo.Context,
        widget: Gtk.DrawingArea,
        model: DockModel,
        config: Config,
        theme: Theme,
        cursor_main: float,
        hide_offset: float = 0.0,
        drag_index: int = -1,
        drop_insert_index: int = -1,
        zoom_progress: float = 1.0,
        hovered_id: str = "",
    ) -> None:
        """Main draw entry point -- called on every 'draw' signal.

        cursor_main is the cursor position along the main axis (the axis
        icons are laid out along). For horizontal docks this is X, for
        vertical docks this is Y.
        """
        alloc = widget.get_allocation()
        width, height = alloc.width, alloc.height

        # Render to offscreen surface, then blit atomically with SOURCE.
        # With set_double_buffered(False), we draw directly to the X11
        # backing surface. CLEAR+draw leaves a transparent gap between
        # frames that the compositor can catch. Offscreen avoids this:
        # the window surface is only touched once (the SOURCE blit).
        offscreen = cr.get_target().create_similar(
            cairo.Content.COLOR_ALPHA, width, height
        )
        ocr = cairo.Context(offscreen)
        self._draw_content(
            ocr,
            width,
            height,
            model,
            config,
            theme,
            cursor_main,
            hide_offset,
            drag_index,
            drop_insert_index,
            zoom_progress,
            hovered_id,
        )
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_surface(offscreen, 0, 0)
        cr.paint()

    def _draw_content(
        self,
        cr: cairo.Context,
        width: int,
        height: int,
        model: DockModel,
        config: Config,
        theme: Theme,
        cursor_main: float,
        hide_offset: float,
        drag_index: int,
        drop_insert_index: int,
        zoom_progress: float,
        hovered_id: str,
    ) -> None:
        """Render all dock content to a Cairo context."""
        pos = config.pos
        horizontal = is_horizontal(pos)
        main_size = width if horizontal else height
        cross_size = height if horizontal else width

        icon_hide = hide_offset
        bg_extra = (
            hide_offset * (cross_size - theme.shelf_height) if hide_offset > 0 else 0.0
        )

        items = model.visible_items()
        if not items:
            return

        num_items = len(items)
        icon_size = config.icon_size

        # Layout in 1D content-space (same for all positions).
        # Matches content_bounds: h_padding + item_padding/2 on each side.
        base_w = (
            (theme.h_padding + theme.item_padding / 2) * 2
            + num_items * icon_size
            + max(0, num_items - 1) * theme.item_padding
        )
        base_offset = (main_size - base_w) / 2
        local_cursor = cursor_main - base_offset if cursor_main >= 0 else -1.0
        layout = compute_layout(
            items,
            config,
            local_cursor,
            item_padding=theme.item_padding,
            h_padding=theme.h_padding,
            zoom_progress=zoom_progress,
        )

        # Content bounds and centering offset along main axis.
        # compute_layout returns positions in content-space (0 = left edge
        # of content). icon_offset translates content-space to window-space
        # by centering the content extent within main_size.
        left_edge, right_edge = content_bounds(
            layout, icon_size, theme.h_padding, theme.item_padding
        )
        zoomed_w = right_edge - left_edge
        # Include the drop gap so shelf expands to cover displaced items
        drop_gap = icon_size + theme.item_padding if drop_insert_index >= 0 else 0
        zoomed_w += drop_gap
        # icon_offset = window_center - content_center, accounting for
        # left_edge so that layout x=0 maps to the correct window pixel
        icon_offset = (main_size - zoomed_w) / 2 - left_edge

        # Shelf width smoothing — snap during hide/show and drop gap so
        # the shelf tracks icon positions exactly (no lag = no edge gaps).
        target_shelf_w = zoomed_w
        if self.smooth_shelf_w == 0.0 or drop_gap > 0 or hide_offset > 0:
            self.smooth_shelf_w = target_shelf_w
        else:
            self.smooth_shelf_w += (
                target_shelf_w - self.smooth_shelf_w
            ) * SHELF_SMOOTH_FACTOR
        shelf_main_extent = self.smooth_shelf_w
        shelf_main_pos = (main_size - shelf_main_extent) / 2

        bg_height = theme.shelf_height

        # --- Draw shelf background with Cairo transform ---
        # Always draw as-if-bottom, then transform for other positions.
        # Shelf slides by the same base offset as icons (icon_hide * cross)
        # plus an extra cascade boost so its top edge hits the screen edge
        # at the same time the icons' top edge does.
        shelf_slide = icon_hide * cross_size + bg_extra
        as_bottom_bg_y = cross_size - bg_height + shelf_slide

        cr.save()
        self._apply_shelf_transform(cr, pos, width, height, main_size, cross_size)
        draw_shelf_background(
            cr, shelf_main_pos, as_bottom_bg_y, shelf_main_extent, bg_height, theme
        )

        # Active glow (drawn in shelf transform space)
        for item, li in zip(items, layout):
            if item.is_active:
                if item.desktop_id not in self._icon_colors:
                    self._icon_colors[item.desktop_id] = average_icon_color(item.icon)
                color = self._icon_colors[item.desktop_id]
                self._draw_active_glow(
                    cr,
                    li,
                    icon_size,
                    icon_offset,
                    as_bottom_bg_y,
                    bg_height,
                    shelf_main_pos,
                    shelf_main_extent,
                    color,
                    theme.glow_opacity,
                )
        cr.restore()

        # --- Draw icons ---
        self._update_slide_offsets(items, layout, icon_offset)

        gap = icon_size + theme.item_padding if drop_insert_index >= 0 else 0
        self._update_hover_lighten(items, hovered_id, theme)

        # Hide offset: distance to push content toward the screen edge
        hide_cross = icon_hide * cross_size

        now = GLib.get_monotonic_time()
        for i, (item, li) in enumerate(zip(items, layout)):
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
                    easing_bounce(lt, launch_duration_us, 2)
                    * icon_size
                    * theme.launch_bounce_height
                )
            urgent_duration_us = theme.urgent_bounce_time_ms * 1000
            if item.last_urgent > 0:
                ut = now - item.last_urgent
                bounce += (
                    easing_bounce(ut, urgent_duration_us, 1)
                    * icon_size
                    * theme.urgent_bounce_height
                )

            scaled_size = icon_size * li.scale
            main_pos = li.x + icon_offset + slide + drop_shift
            ix, iy = map_icon_position(
                pos,
                main_pos,
                cross_size,
                theme.bottom_padding,
                scaled_size,
                hide_cross,
                bounce,
            )
            self._draw_icon(cr, item, li, icon_size, ix, iy, lighten, darken)

        # --- Draw indicators ---
        for i, (item, li) in enumerate(zip(items, layout)):
            if item.is_running:
                slide = self.slide_offsets.get(item.desktop_id, 0.0)
                drop_shift = (
                    gap if drop_insert_index >= 0 and i >= drop_insert_index else 0
                )
                self._draw_indicator(
                    cr,
                    item,
                    li,
                    icon_size,
                    icon_offset + slide + drop_shift,
                    cross_size,
                    hide_cross,
                    theme,
                    pos,
                )

        # --- Urgent glow at screen edge (only when fully hidden) ---
        if hide_offset >= 1.0:
            for item, li in zip(items, layout):
                if item.last_urgent > 0:
                    elapsed = now - item.last_urgent
                    opacity = compute_urgent_glow_opacity(
                        elapsed, theme.urgent_glow_time_ms, theme.urgent_glow_pulse_ms
                    )
                    if opacity > 0:
                        if item.desktop_id not in self._icon_colors:
                            self._icon_colors[item.desktop_id] = average_icon_color(
                                item.icon
                            )
                        color = self._icon_colors[item.desktop_id]
                        self._draw_urgent_glow(
                            cr,
                            li,
                            icon_size,
                            icon_offset,
                            cross_size,
                            pos,
                            theme,
                            color,
                            opacity,
                        )

    @staticmethod
    def _apply_shelf_transform(
        cr: cairo.Context,
        pos: Position,
        width: int,
        height: int,
        main_size: int,
        cross_size: int,
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
        fade_frames = max(1, theme.active_time_ms // 16)
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
        for item, li in zip(items, layout):
            new_positions[item.desktop_id] = li.x + icon_offset

        # When an item jumps to a new position (reorder, add, remove),
        # store the displacement as a slide offset. The icon renders at
        # (new_position + slide_offset), starting where it was and
        # decaying toward 0 over subsequent frames.
        for desktop_id, new_x in new_positions.items():
            old_x = self.prev_positions.get(desktop_id)
            if old_x is not None and abs(old_x - new_x) > SLIDE_MOVE_THRESHOLD:
                current_slide = self.slide_offsets.get(desktop_id, 0.0)
                self.slide_offsets[desktop_id] = current_slide + (old_x - new_x)

        # Exponential decay: each frame multiplies the offset by
        # SLIDE_DECAY_FACTOR (0.75), giving a quick ease-out.
        # Offsets below SLIDE_CLEAR_THRESHOLD are removed to avoid
        # sub-pixel drift and stale entries.
        decay = SLIDE_DECAY_FACTOR
        dead = []
        for desktop_id in self.slide_offsets:
            self.slide_offsets[desktop_id] *= decay
            if abs(self.slide_offsets[desktop_id]) < SLIDE_CLEAR_THRESHOLD:
                dead.append(desktop_id)
        for d in dead:
            del self.slide_offsets[d]

        self.prev_positions = new_positions

    @staticmethod
    def _draw_icon(
        cr: cairo.Context,
        item: DockItem,
        li: LayoutItem,
        base_size: int,
        x: float,
        y: float,
        lighten: float = 0.0,
        darken: float = 0.0,
    ) -> None:
        """Draw a single dock icon at (x, y) with hover/click effects."""
        if item.icon is None:
            return

        scaled_size = base_size * li.scale
        icon_width = item.icon.get_width()
        icon_height = item.icon.get_height()

        icon_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, icon_width, icon_height)
        icon_cr = cairo.Context(icon_surface)

        Gdk.cairo_set_source_pixbuf(icon_cr, item.icon, 0, 0)
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
        glow_pad = glow_width * 0.15

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
        grad.add_color_stop_rgba(0.33, r, g, b, 0.66)
        grad.add_color_stop_rgba(0.66, r, g, b, 0.33)
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
        base_size: int,
        main_pos: float,
        cross_size: float,
        hide_cross: float,
        theme: Theme,
        pos: Position,
    ) -> None:
        """Draw running indicator dot(s) near the screen edge."""
        scaled_size = base_size * li.scale
        main_center = li.x + main_pos + scaled_size / 2
        edge_padding = theme.bottom_padding

        color = (
            theme.active_indicator_color if item.is_active else theme.indicator_color
        )
        cr.set_source_rgba(*color)

        count = min(item.instance_count, theme.max_indicator_dots)
        spacing = theme.indicator_radius * INDICATOR_SPACING_MULT

        if pos == Position.BOTTOM:
            cx = main_center
            cy = cross_size - edge_padding / 2 + hide_cross
            for j in range(count):
                dx = cx + (j - (count - 1) / 2) * spacing
                cr.arc(dx, cy, theme.indicator_radius, 0, 2 * math.pi)
                cr.fill()
        elif pos == Position.TOP:
            cx = main_center
            cy = edge_padding / 2 - hide_cross
            for j in range(count):
                dx = cx + (j - (count - 1) / 2) * spacing
                cr.arc(dx, cy, theme.indicator_radius, 0, 2 * math.pi)
                cr.fill()
        elif pos == Position.LEFT:
            cx = edge_padding / 2 - hide_cross
            cy = main_center
            for j in range(count):
                dy = cy + (j - (count - 1) / 2) * spacing
                cr.arc(cx, dy, theme.indicator_radius, 0, 2 * math.pi)
                cr.fill()
        else:  # RIGHT
            cx = cross_size - edge_padding / 2 + hide_cross
            cy = main_center
            for j in range(count):
                dy = cy + (j - (count - 1) / 2) * spacing
                cr.arc(cx, dy, theme.indicator_radius, 0, 2 * math.pi)
                cr.fill()
