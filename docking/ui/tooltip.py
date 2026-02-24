"""Tooltip manager -- custom positioned tooltips near dock icons."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk  # noqa: E402

from docking.core.position import Position, is_horizontal

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.core.zoom import LayoutItem
    from docking.platform.model import DockModel, DockItem


TOOLTIP_GAP = 10


def compute_tooltip_position(
    pos: Position,
    anchor_x: float,
    anchor_y: float,
    tooltip_w: int,
    tooltip_h: int,
) -> tuple[int, int]:
    """Compute tooltip (x, y) before screen clamping.

    anchor is the icon edge closest to the tooltip:
    - BOTTOM: anchor = (icon_center_x, icon_top_y)
    - TOP:    anchor = (icon_center_x, icon_bottom_y)
    - LEFT:   anchor = (icon_right_x, icon_center_y)
    - RIGHT:  anchor = (icon_left_x, icon_center_y)
    """
    if pos == Position.BOTTOM:
        return int(anchor_x - tooltip_w / 2), int(anchor_y - tooltip_h - TOOLTIP_GAP)
    elif pos == Position.TOP:
        return int(anchor_x - tooltip_w / 2), int(anchor_y + TOOLTIP_GAP)
    elif pos == Position.LEFT:
        return int(anchor_x + TOOLTIP_GAP), int(anchor_y - tooltip_h / 2)
    else:  # RIGHT
        return int(anchor_x - tooltip_w - TOOLTIP_GAP), int(anchor_y - tooltip_h / 2)


class TooltipManager:
    """Custom positioned tooltip shown near hovered dock icons.

    Tooltip is placed on the inner side (away from screen edge):
    BOTTOM: above icon. TOP: below. LEFT: right of. RIGHT: left of.
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
        """Show or hide the app name tooltip near the hovered icon."""
        if not item or not item.name:
            self.hide()
            return

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
            layout,
            self._config.icon_size,
            self._theme.h_padding,
            self._theme.item_padding,
        )
        zoomed_w = right_edge - left_edge

        pos = self._config.pos
        horizontal = is_horizontal(pos)
        if horizontal:
            main_win_size = self._window.get_size()[0]
        else:
            main_win_size = self._window.get_size()[1]
        offset = (main_win_size - zoomed_w) / 2 - left_edge

        scaled_size = li.scale * self._config.icon_size
        edge_padding = self._theme.bottom_padding
        win_x, win_y = self._window.get_position()
        win_w, win_h = self._window.get_size()

        if pos == Position.BOTTOM:
            icon_center_x = win_x + li.x + offset + scaled_size / 2
            icon_top_y = win_y + win_h - edge_padding - scaled_size
            self._show_tooltip(item.name, pos, icon_center_x, icon_top_y)
        elif pos == Position.TOP:
            icon_center_x = win_x + li.x + offset + scaled_size / 2
            icon_bottom_y = win_y + edge_padding + scaled_size
            self._show_tooltip(item.name, pos, icon_center_x, icon_bottom_y)
        elif pos == Position.LEFT:
            icon_center_y = win_y + li.x + offset + scaled_size / 2
            icon_right_x = win_x + edge_padding + scaled_size
            self._show_tooltip(item.name, pos, icon_right_x, icon_center_y)
        else:  # RIGHT
            icon_center_y = win_y + li.x + offset + scaled_size / 2
            icon_left_x = win_x + win_w - edge_padding - scaled_size
            self._show_tooltip(item.name, pos, icon_left_x, icon_center_y)

    def _show_tooltip(
        self, text: str, pos: Position, anchor_x: float, anchor_y: float
    ) -> None:
        """Display a tooltip near anchor point, on the inner side of the dock."""
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

            def on_draw(widget, cr):
                alloc = widget.get_allocation()
                radius = 6
                w, h = alloc.width, alloc.height
                cr.new_sub_path()
                cr.arc(w - radius, radius, radius, -math.pi / 2, 0)
                cr.arc(w - radius, h - radius, radius, 0, math.pi / 2)
                cr.arc(radius, h - radius, radius, math.pi / 2, math.pi)
                cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
                cr.close_path()
                cr.set_source_rgba(0, 0, 0, 0.85)
                cr.fill()
                return False

            self._tooltip_window.connect("draw", on_draw)

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

        pref = self._tooltip_window.get_preferred_size()[1]
        tw = max(pref.width, 1)
        th = max(pref.height, 1)

        tx, ty = compute_tooltip_position(pos, anchor_x, anchor_y, tw, th)

        # Clamp to screen
        screen = self._tooltip_window.get_screen()
        screen_w = screen.get_width()
        screen_h = screen.get_height()
        tx = max(0, min(tx, screen_w - tw))
        ty = max(0, min(ty, screen_h - th))

        self._tooltip_window.move(tx, ty)
        self._tooltip_window.show_all()

    def hide(self) -> None:
        """Hide the tooltip window."""
        if self._tooltip_window:
            self._tooltip_window.hide()
