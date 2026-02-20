"""Cairo renderer for the dock — background, icons, indicators, zoom visualization."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import cairo

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf  # noqa: E402

if TYPE_CHECKING:
    from docking.config import Config
    from docking.dock_model import DockModel, DockItem
    from docking.theme import Theme
    from docking.zoom import LayoutItem


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


class DockRenderer:
    """Stateless renderer: given state, draws the dock via Cairo."""

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
        from docking.zoom import total_width
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

        # Compute layout
        from docking.zoom import compute_layout
        layout = compute_layout(
            items, config, cursor_x,
            item_padding=theme.item_padding,
            h_padding=theme.h_padding,
        )

        # Draw background — shorter shelf at the bottom, icons overflow above
        bg_height = config.icon_size * 0.55 + theme.bottom_padding
        bg_y = h - bg_height
        self._draw_background(cr, 0, bg_y, w, bg_height, theme)

        # Draw icons
        icon_size = config.icon_size
        for i, (item, li) in enumerate(zip(items, layout)):
            if i == drag_index:
                continue
            self._draw_icon(cr, item, li, icon_size, h, theme)

        # Draw indicators
        for i, (item, li) in enumerate(zip(items, layout)):
            if item.is_running:
                self._draw_indicator(cr, item, li, icon_size, h, theme)

    def _draw_background(
        self, cr: cairo.Context, x: float, y: float, w: float, h: float, theme: Theme,
    ) -> None:
        """Draw the dock background shelf with gradient fill and stroke."""
        r = theme.roundness
        margin = theme.stroke_width / 2
        _rounded_rect(cr, x + margin, y + margin, w - 2 * margin, h - 2 * margin, r, round_bottom=False)

        # Vertical gradient fill
        pat = cairo.LinearGradient(0, y, 0, y + h)
        pat.add_color_stop_rgba(0, *theme.fill_start)
        pat.add_color_stop_rgba(1, *theme.fill_end)
        cr.set_source(pat)
        cr.fill_preserve()

        # Stroke
        cr.set_source_rgba(*theme.stroke)
        cr.set_line_width(theme.stroke_width)
        cr.stroke()

    def _draw_icon(
        self,
        cr: cairo.Context,
        item: DockItem,
        li: LayoutItem,
        base_size: int,
        dock_height: float,
        theme: Theme,
    ) -> None:
        """Draw a single dock icon at its zoomed position and scale."""
        if item.icon is None:
            return

        scaled_size = base_size * li.scale
        # Vertically align: icons sit on the bottom, pushed up by bottom_padding
        y = dock_height - theme.bottom_padding - scaled_size

        cr.save()
        cr.translate(li.x, y)

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
    ) -> None:
        """Draw running indicator dot(s) below an icon."""
        scaled_size = base_size * li.scale
        center_x = li.x + scaled_size / 2
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
