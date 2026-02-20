"""Cairo renderer for the dock — background, icons, indicators, zoom visualization."""

from __future__ import annotations

import math
from docking.log import get_logger

log = get_logger("renderer")
from typing import TYPE_CHECKING

import cairo

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf  # noqa: E402

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.model import DockModel, DockItem
    from docking.core.theme import Theme
    from docking.core.zoom import LayoutItem


def _rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float,
                   round_bottom: bool = True) -> None:
    """Draw a rounded rectangle path, optionally with square bottom corners."""
    cr.new_sub_path()
    # Top-right (rounded)
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    if round_bottom:
        # Bottom-right (rounded)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        # Bottom-left (rounded)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    else:
        # Bottom-right (square)
        cr.line_to(x + w, y + h)
        # Bottom-left (square)
        cr.line_to(x, y + h)
    # Top-left (rounded)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


SLIDE_DURATION_MS = 300
SLIDE_FRAME_MS = 16


class DockRenderer:
    """Dock renderer with per-item slide animation for reordering."""

    def __init__(self) -> None:
        # Per-item X offset for slide animation: {desktop_id: offset_px}
        self._slide_offsets: dict[str, float] = {}
        self._prev_positions: dict[str, float] = {}  # {desktop_id: last_x}

    def compute_dock_size(
        self, model: DockModel, config: Config, theme: Theme,
    ) -> tuple[int, int]:
        """Compute base dock dimensions (no zoom)."""
        items = model.visible_items()
        n = len(items)
        icon_size = config.icon_size
        width = int(
            theme.h_padding * 2
            + n * icon_size
            + max(0, n - 1) * theme.item_padding
        )
        height = int(icon_size + theme.top_padding + theme.bottom_padding)
        return max(width, 1), max(height, 1)

    def compute_zoomed_width(
        self, layout: list[LayoutItem], config: Config, theme: Theme,
    ) -> int:
        """Compute total dock width from a zoomed layout."""
        from docking.core.zoom import total_width
        w = total_width(layout, config.icon_size, theme.item_padding, theme.h_padding)
        return max(int(w), 1)

    def draw(
        self,
        cr: cairo.Context,
        widget: Gtk.DrawingArea,
        model: DockModel,
        config: Config,
        theme: Theme,
        cursor_x: float,
        hide_offset: float = 0.0,
        drag_index: int = -1,
        drop_insert_index: int = -1,
    ) -> None:
        """Main draw entry point — called on every 'draw' signal."""
        alloc = widget.get_allocation()
        w, h = alloc.width, alloc.height

        # Clear to transparent
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        # Apply hide offset (slides dock downward)
        if hide_offset > 0:
            cr.translate(0, hide_offset * h)

        items = model.visible_items()
        if not items:
            return

        from docking.core.zoom import compute_layout, total_width
        n = len(items)

        # Base offset for cursor conversion (content-space)
        base_w = theme.h_padding * 2 + n * config.icon_size + max(0, n - 1) * theme.item_padding
        base_offset = (w - base_w) / 2

        # Compute zoomed layout in content-space
        local_cursor = cursor_x - base_offset if cursor_x >= 0 else -1.0
        layout = compute_layout(
            items, config, local_cursor,
            item_padding=theme.item_padding,
            h_padding=theme.h_padding,
        )

        # Compute actual content bounds (accounts for leftward displacement)
        from docking.core.zoom import content_bounds
        left_edge, right_edge = content_bounds(layout, config.icon_size, theme.h_padding)
        zoomed_w = right_edge - left_edge
        # icon_offset: shifts layout so content is centered in window
        icon_offset = (w - zoomed_w) / 2 - left_edge

        # Shelf matches icons but smoothed to reduce wobble
        target_shelf_w = zoomed_w
        if not hasattr(self, '_smooth_shelf_w'):
            self._smooth_shelf_w = base_w
        self._smooth_shelf_w += (target_shelf_w - self._smooth_shelf_w) * 0.3
        shelf_w = self._smooth_shelf_w
        shelf_x = (w - shelf_w) / 2

        # Plank Yaru-light: bg_height ≈ 21px for 48px icons (ratio ~0.44)
        bg_height = config.icon_size * 0.44 + theme.bottom_padding
        bg_y = h - bg_height
        self._draw_background(cr, shelf_x, bg_y, shelf_w, bg_height, theme)

        # Update slide animation offsets (detect items that moved)
        self._update_slide_offsets(items, layout, icon_offset)

        # Gap for external drop insertion
        gap = config.icon_size + theme.item_padding if drop_insert_index >= 0 else 0

        # Draw icons with slide offset + drop gap
        icon_size = config.icon_size
        for i, (item, li) in enumerate(zip(items, layout)):
            if i == drag_index:
                continue
            slide = self._slide_offsets.get(item.desktop_id, 0.0)
            drop_shift = gap if drop_insert_index >= 0 and i >= drop_insert_index else 0
            self._draw_icon(cr, item, li, icon_size, h, theme, icon_offset + slide + drop_shift)

        # Draw indicators with slide offset + drop gap
        for i, (item, li) in enumerate(zip(items, layout)):
            if item.is_running:
                slide = self._slide_offsets.get(item.desktop_id, 0.0)
                drop_shift = gap if drop_insert_index >= 0 and i >= drop_insert_index else 0
                self._draw_indicator(cr, item, li, icon_size, h, theme, icon_offset + slide + drop_shift)

    def _update_slide_offsets(self, items: list, layout: list, icon_offset: float) -> None:
        """Detect items that changed position and set slide animation offsets."""
        new_positions: dict[str, float] = {}
        for item, li in zip(items, layout):
            new_positions[item.desktop_id] = li.x + icon_offset

        for desktop_id, new_x in new_positions.items():
            old_x = self._prev_positions.get(desktop_id)
            if old_x is not None and abs(old_x - new_x) > 2.0:
                # Item moved — set offset so it appears at old position, then animates
                current_slide = self._slide_offsets.get(desktop_id, 0.0)
                self._slide_offsets[desktop_id] = current_slide + (old_x - new_x)

        # Decay all offsets toward 0 (lerp)
        decay = 0.75  # per-frame decay factor (~300ms to settle at 60fps)
        dead = []
        for desktop_id in self._slide_offsets:
            self._slide_offsets[desktop_id] *= decay
            if abs(self._slide_offsets[desktop_id]) < 0.5:
                dead.append(desktop_id)
        for d in dead:
            del self._slide_offsets[d]

        self._prev_positions = new_positions

    def _draw_background(
        self, cr: cairo.Context, x: float, y: float, w: float, h: float, theme: Theme,
    ) -> None:
        """Draw the dock background shelf with Plank-style 3D effect.

        Three layers: gradient fill, dark outer stroke, inner highlight stroke.
        """
        r = theme.roundness
        lw = theme.stroke_width

        # Layer 1: Gradient fill + outer stroke
        _rounded_rect(cr, x + lw / 2, y + lw / 2, w - lw, h - lw / 2, r, round_bottom=False)

        pat = cairo.LinearGradient(0, y, 0, y + h)
        pat.add_color_stop_rgba(0, *theme.fill_start)
        pat.add_color_stop_rgba(1, *theme.fill_end)
        cr.set_source(pat)
        cr.fill_preserve()

        cr.set_source_rgba(*theme.stroke)
        cr.set_line_width(lw)
        cr.stroke()

        # Layer 2: Inner highlight stroke (creates 3D bevel effect)
        # Plank uses white with varying opacity: 50% top → 12% → 8% → 19% bottom
        is_r, is_g, is_b, _ = theme.inner_stroke
        inset = 3 * lw / 2
        inner_h = h - inset
        top_point = max(r, lw) / h if h > 0 else 0.1
        bottom_point = 1.0 - top_point

        highlight = cairo.LinearGradient(0, y + inset, 0, y + h - inset)
        highlight.add_color_stop_rgba(0, is_r, is_g, is_b, 0.5)
        highlight.add_color_stop_rgba(top_point, is_r, is_g, is_b, 0.12)
        highlight.add_color_stop_rgba(bottom_point, is_r, is_g, is_b, 0.08)
        highlight.add_color_stop_rgba(1, is_r, is_g, is_b, 0.19)

        inner_r = max(r - lw, 0)
        _rounded_rect(cr, x + inset, y + inset, w - 2 * inset, inner_h - inset / 2, inner_r, round_bottom=False)
        cr.set_source(highlight)
        cr.set_line_width(lw)
        cr.stroke()

    def _draw_icon(
        self,
        cr: cairo.Context,
        item: DockItem,
        li: LayoutItem,
        base_size: int,
        dock_height: float,
        theme: Theme,
        x_offset: float = 0.0,
    ) -> None:
        """Draw a single dock icon at its zoomed position and scale."""
        if item.icon is None:
            return

        scaled_size = base_size * li.scale
        y = dock_height - theme.bottom_padding - scaled_size

        cr.save()
        cr.translate(li.x + x_offset, y)

        # Scale the icon pixbuf
        icon_w = item.icon.get_width()
        icon_h = item.icon.get_height()
        sx = scaled_size / icon_w
        sy = scaled_size / icon_h
        cr.scale(sx, sy)

        Gdk.cairo_set_source_pixbuf(cr, item.icon, 0, 0)
        cr.paint()
        cr.restore()

    def _draw_indicator(
        self,
        cr: cairo.Context,
        item: DockItem,
        li: LayoutItem,
        base_size: int,
        dock_height: float,
        theme: Theme,
        x_offset: float = 0.0,
    ) -> None:
        """Draw running indicator dot(s) below an icon."""
        scaled_size = base_size * li.scale
        center_x = li.x + x_offset + scaled_size / 2
        y = dock_height - theme.bottom_padding / 2

        color = theme.active_indicator_color if item.is_active else theme.indicator_color
        cr.set_source_rgba(*color)

        count = min(item.instance_count, 3)
        spacing = theme.indicator_radius * 3
        start_x = center_x - (count - 1) * spacing / 2

        for j in range(count):
            dot_x = start_x + j * spacing
            cr.arc(dot_x, y, theme.indicator_radius, 0, 2 * math.pi)
            cr.fill()
