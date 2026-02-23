"""Tooltip manager — custom positioned tooltips above dock icons."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.core.zoom import LayoutItem
    from docking.platform.model import DockModel, DockItem


# Tooltip gap between icon top and tooltip bottom (matches Plank's PADDING=10)
TOOLTIP_GAP = 10


class TooltipManager:
    """Custom positioned tooltip shown above hovered dock icons.

    GTK tooltips (set_tooltip_text / query-tooltip signal) cannot be
    precisely positioned — GTK places them using its own heuristics
    that don't account for zoomed icon sizes or the dock's coordinate
    system. The tooltip would appear at the wrong X position (centered
    on the un-zoomed icon) and at a fixed Y offset from the cursor
    rather than above the icon's actual top edge.

    Instead, we create a separate Gtk.Window(POPUP) with:
    - RGBA visual for transparency (same as the dock window)
    - A Cairo-drawn rounded rectangle background (dark, 85% opaque)
    - Manual positioning: centered horizontally over the icon,
      placed TOOLTIP_GAP pixels above the icon's top edge
    - Screen-edge clamping so it never goes off-screen
    """

    def __init__(
        self,
        window: Gtk.Window,
        config: Config,
        model: DockModel,
        theme: Theme,
    ) -> None:
        self._window = window
        self._config = config
        self._model = model
        self._theme = theme
        self._tooltip_window: Gtk.Window | None = None

    def update(self, item: DockItem | None, layout: list[LayoutItem]) -> None:
        """Show or hide the app name tooltip centered above the hovered icon."""
        if not item or not item.name:
            self.hide()
            return

        # Find the icon's screen position
        items = self._model.visible_items()
        idx = None
        for i, it in enumerate(items):
            if it is item:
                idx = i
                break
        if idx is None or idx >= len(layout):
            self.hide()
            return

        li = layout[idx]
        from docking.core.zoom import content_bounds

        left_edge, right_edge = content_bounds(
            layout, self._config.icon_size, self._theme.h_padding
        )
        zoomed_w = right_edge - left_edge
        window_w: int = self._window.get_size()[0]
        offset = (window_w - zoomed_w) / 2 - left_edge

        scaled_size = li.scale * self._config.icon_size

        # Compute the icon's top edge in screen coordinates.
        # The dock window's bottom edge sits at the screen bottom. Icons
        # are drawn from bottom up: icon_top = win_bottom - bottom_padding - icon_height
        win_x, win_y = self._window.get_position()
        _, win_h = self._window.get_size()
        screen_bottom = win_y + win_h
        icon_top_y = screen_bottom - self._theme.bottom_padding - scaled_size

        icon_center_x = win_x + li.x + offset + scaled_size / 2

        self.show(item.name, icon_center_x, icon_top_y)

    def show(self, text: str, center_x: float, above_y: float) -> None:
        """Display a tooltip centered above a screen point, 10px gap.

        The tooltip window requires an RGBA visual (compositing support)
        for the semi-transparent Cairo-drawn background to work. Without
        RGBA, the rounded corners would show opaque black rectangles
        instead of transparency. The RGBA visual is set once on first
        creation and reused for subsequent tooltip updates.
        """
        if self._tooltip_window is None:
            self._tooltip_window = Gtk.Window(type=Gtk.WindowType.POPUP)
            self._tooltip_window.set_decorated(False)
            self._tooltip_window.set_skip_taskbar_hint(True)
            self._tooltip_window.set_resizable(False)
            self._tooltip_window.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
            self._tooltip_window.set_app_paintable(True)

            screen = self._tooltip_window.get_screen()
            visual = screen.get_rgba_visual()
            if visual:
                self._tooltip_window.set_visual(visual)

            # Dark tooltip with rounded corners — draw background via Cairo
            # since app_paintable windows skip GTK's default background
            def on_draw(widget, cr):
                alloc = widget.get_allocation()
                # Rounded rect background
                radius = 6
                width, height = alloc.width, alloc.height
                cr.new_sub_path()
                cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
                cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
                cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
                cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
                cr.close_path()
                cr.set_source_rgba(0, 0, 0, 0.85)
                cr.fill()
                return False  # propagate to draw children

            self._tooltip_window.connect("draw", on_draw)

        # Update label
        child = self._tooltip_window.get_child()
        if child:
            self._tooltip_window.remove(child)
        label = Gtk.Label(label=text)
        label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        label.set_margin_start(6)
        label.set_margin_end(6)
        label.set_margin_top(6)
        label.set_margin_bottom(6)
        self._tooltip_window.add(label)
        label.show()

        # Measure size, position, THEN show (avoids flash at 0,0)
        label.show()
        pref = self._tooltip_window.get_preferred_size()[1]
        tooltip_width = max(pref.width, 1)
        tooltip_height = max(pref.height, 1)
        tooltip_x = int(center_x - tooltip_width / 2)
        tooltip_y = int(above_y - tooltip_height - TOOLTIP_GAP)

        # Clamp to screen
        screen_w = self._tooltip_window.get_screen().get_width()
        tooltip_x = max(0, min(tooltip_x, screen_w - tooltip_width))
        tooltip_y = max(0, tooltip_y)

        self._tooltip_window.move(tooltip_x, tooltip_y)
        self._tooltip_window.show_all()

    def hide(self) -> None:
        """Hide the tooltip window."""
        if self._tooltip_window:
            self._tooltip_window.hide()
