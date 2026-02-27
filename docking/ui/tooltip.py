"""Tooltip manager -- custom positioned tooltips near dock icons."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from docking.core.position import Position, is_horizontal
from docking.log import get_logger

_log = get_logger(name="tooltip")

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.core.zoom import LayoutItem
    from docking.platform.model import DockItem, DockModel


TOOLTIP_BASE_GAP = 10  # base gap between icon and tooltip


def compute_tooltip_position(
    pos: Position,
    anchor_x: float,
    anchor_y: float,
    tooltip_w: int,
    tooltip_h: int,
    gap: float = TOOLTIP_BASE_GAP,
) -> tuple[int, int]:
    """Compute tooltip (x, y) before screen clamping.

    anchor is the icon edge closest to the tooltip:
    - BOTTOM: anchor = (icon_center_x, icon_top_y)
    - TOP:    anchor = (icon_center_x, icon_bottom_y)
    - LEFT:   anchor = (icon_right_x, icon_center_y)
    - RIGHT:  anchor = (icon_left_x, icon_center_y)

    gap includes bounce headroom so the tooltip doesn't overlap a
    bouncing icon.
    """
    if pos == Position.BOTTOM:
        return int(anchor_x - tooltip_w / 2), int(anchor_y - tooltip_h - gap)
    elif pos == Position.TOP:
        return int(anchor_x - tooltip_w / 2), int(anchor_y + gap)
    elif pos == Position.LEFT:
        return int(anchor_x + gap), int(anchor_y - tooltip_h / 2)
    else:  # RIGHT
        return int(anchor_x - tooltip_w - gap), int(anchor_y - tooltip_h / 2)


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
        # Track the last shown item and its name to avoid rebuilding the
        # tooltip on every motion event when hovering the same item. The
        # name is tracked separately because applets can change item.name
        # dynamically (e.g. clippy updates the tooltip on scroll).
        self._last_item: DockItem | None = None
        self._last_name: str = ""

    def update(self, item: DockItem | None, layout: list[LayoutItem]) -> None:
        """Show or reposition tooltip for the hovered icon.

        When item is None (cursor in gap between icons), keeps the last
        tooltip visible to avoid flicker. The dock's _on_leave hides it
        when the mouse actually exits the dock.
        """
        if not item or not item.name:
            return

        # Check if content needs rebuilding (expensive: show_all triggers
        # crossing events) vs just repositioning (cheap: move only).
        content_changed = not (item is self._last_item and item.name == self._last_name)
        if content_changed:
            _log.debug("content changed: %s", item.name)
        self._last_item = item
        self._last_name = item.name

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
            layout=layout,
            icon_size=self._config.icon_size,
            h_padding=self._theme.h_padding,
            item_padding=self._theme.item_padding,
        )
        zoomed_w = right_edge - left_edge

        pos = self._config.pos
        horizontal = is_horizontal(pos=pos)
        if horizontal:
            main_win_size = self._window.get_size()[0]
        else:
            main_win_size = self._window.get_size()[1]
        offset = (main_win_size - zoomed_w) / 2 - left_edge

        scaled_size = li.scale * self._config.icon_size
        edge_padding = self._theme.bottom_padding
        win_x, win_y = self._window.get_position()
        win_w, win_h = self._window.get_size()

        # Only rebuild widget content when item or text changed
        widget = None
        if content_changed:
            widget = item.tooltip_builder() if item.tooltip_builder else None

        if pos == Position.BOTTOM:
            icon_center_x = win_x + li.x + offset + scaled_size / 2
            icon_top_y = win_y + win_h - edge_padding - scaled_size
            self._show_tooltip(
                text=item.name,
                pos=pos,
                anchor_x=icon_center_x,
                anchor_y=icon_top_y,
                widget=widget,
                content_changed=content_changed,
            )
        elif pos == Position.TOP:
            icon_center_x = win_x + li.x + offset + scaled_size / 2
            icon_bottom_y = win_y + edge_padding + scaled_size
            self._show_tooltip(
                text=item.name,
                pos=pos,
                anchor_x=icon_center_x,
                anchor_y=icon_bottom_y,
                widget=widget,
                content_changed=content_changed,
            )
        elif pos == Position.LEFT:
            icon_center_y = win_y + li.x + offset + scaled_size / 2
            icon_right_x = win_x + edge_padding + scaled_size
            self._show_tooltip(
                text=item.name,
                pos=pos,
                anchor_x=icon_right_x,
                anchor_y=icon_center_y,
                widget=widget,
                content_changed=content_changed,
            )
        else:  # RIGHT
            icon_center_y = win_y + li.x + offset + scaled_size / 2
            icon_left_x = win_x + win_w - edge_padding - scaled_size
            self._show_tooltip(
                text=item.name,
                pos=pos,
                anchor_x=icon_left_x,
                anchor_y=icon_center_y,
                widget=widget,
                content_changed=content_changed,
            )

    def _show_tooltip(
        self,
        text: str,
        pos: Position,
        anchor_x: float,
        anchor_y: float,
        widget: Gtk.Widget | None = None,
        content_changed: bool = True,
    ) -> None:
        """Create/reuse a popup window and display it near the anchor point.

        When content_changed is False, skips the expensive widget rebuild
        (which triggers show_all and crossing events) and only repositions.
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
            content_changed = True  # first show always needs content

        if content_changed:
            # Hide while swapping content to prevent ghost frame at old
            # position with new (differently-sized) content.
            was_visible = self._tooltip_window.get_visible()
            if was_visible:
                self._tooltip_window.hide()

            child = self._tooltip_window.get_child()
            if child:
                self._tooltip_window.remove(child)

            if widget:
                content = widget
            else:
                content = Gtk.Label(label=text)
                content.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            content.set_margin_start(6)
            content.set_margin_end(6)
            content.set_margin_top(6)
            content.set_margin_bottom(6)
            self._tooltip_window.add(content)
            # Realize child so get_preferred_size returns the new
            # content's dimensions, not the previous tooltip's.
            content.show_all()

        pref = self._tooltip_window.get_preferred_size()[1]
        tw = max(pref.width, 1)
        th = max(pref.height, 1)

        # Gap = base gap + half bounce headroom (icon only briefly reaches peak)
        bounce_px = self._config.icon_size * self._theme.launch_bounce_height
        gap = TOOLTIP_BASE_GAP + bounce_px * 0.5
        tx, ty = compute_tooltip_position(
            pos=pos,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            tooltip_w=tw,
            tooltip_h=th,
            gap=gap,
        )

        # Clamp to screen
        screen = self._tooltip_window.get_screen()
        screen_w = screen.get_width()
        screen_h = screen.get_height()
        tx = max(0, min(tx, screen_w - tw))
        ty = max(0, min(ty, screen_h - th))

        _log.debug(
            "pos=(%d,%d) anchor=(%.0f,%.0f) size=%dx%d rebuild=%s",
            tx,
            ty,
            anchor_x,
            anchor_y,
            tw,
            th,
            content_changed,
        )
        self._tooltip_window.move(tx, ty)
        self._tooltip_window.show_all()

    def hide(self) -> None:
        """Hide the tooltip window and clear tracking state."""
        self._last_item = None
        self._last_name = ""
        if self._tooltip_window:
            self._tooltip_window.hide()
