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

"""Tooltip management for dock items, anchored from shared geometry.

What a tooltip means in this dock

The dock tooltip is intentionally simple:

- it is visual only,
- it is anchored to one hovered item,
- it must never become a second interactive surface that competes with the dock,
- it must follow shared geometry instead of inventing its own icon positions.

That sounds straightforward, but dock tooltips have a few failure modes that
ordinary GTK widgets do not:

- showing too early while the dock itself is still animating,
- rebuilding too aggressively while the pointer churns across adjacent items,
- staying visible after the dock has already hidden,
- looking attached to the wrong icon because the hover changed faster than the
  popup content could be rebuilt.

This module exists to manage those problems explicitly.

What this module owns

TooltipManager owns:

- delayed/coalesced tooltip popup rebuilds,
- tooltip window creation and reuse,
- tooltip text/widget content replacement,
- tooltip positioning from item anchor points,
- screen clamping,
- hide/cancel logic.

It does not own:

- hover decisions,
- whether the dock should remain visible,
- autohide policy,
- item geometry itself.

Those come from HoverManager, interaction policy, and shared geometry.

Anchor model

Tooltips are positioned from an item anchor, not from ad hoc event coordinates.
The anchor is the icon edge closest to the tooltip:

    bottom dock:
        tooltip
           ^
           |
        [ icon ]
    ---------------- screen edge

    top dock:
    ---------------- screen edge
        [ icon ]
           |
           v
        tooltip

    left dock:
    screen edge | [ icon ] -> tooltip

    right dock:
    tooltip <- [ icon ] | screen edge

The shared geometry frame supplies:

- anchor_x
- anchor_y

That is why the tooltip does not need to know how icons are laid out or zoomed.

Immediate reposition vs delayed rebuild

There are two tooltip update modes:

1. Reposition only
   Same item, same text. Move the popup cheaply.

2. Rebuild content
   New item or changed label/widget. This is more expensive because GTK widget
   rebuilding and `show_all()` can trigger crossing events and popup churn.

The manager therefore treats these paths differently:

    same item/text
      -> move only

    changed item/text
      -> coalesce through idle callback

That coalescing matters when the pointer moves quickly across adjacent icons.
Without it, the tooltip can briefly show the last item the hover touched even
though the pointer already moved on.

Why this module reuses one popup window

Creating and destroying popup windows repeatedly is expensive and noisy.
Reusing a single popup window gives:

- less GTK churn,
- more stable positioning,
- fewer crossing side effects,
- less visual flashing.

So the common path is:

    create once
      |
      +--> replace contents as needed
      +--> move popup as hover changes
      +--> hide when hover/dock policy requires

Tooltip and autohide

Tooltips do not keep the dock open. That is an important invariant.

Previews are different because they are an intended continuation of interaction.
Tooltips are not. They are decoration and context.

So the intended behavior is:

    tooltip visible
      !=
    dock should remain shown

That means this module must cooperate with hover/interaction policy rather than
attempting to own dock visibility.

Why screen clamping belongs here

The geometry frame gives the ideal anchor. But the tooltip is a real popup
window that can run off-screen on small displays or near monitor edges.

That last step is tooltip-specific:

    ideal anchor position
        |
        +--> compute orientation-relative placement
        |
        +--> clamp to screen bounds

That is why screen clamping belongs in this module and not in the shared dock
geometry model.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.core.position import Position
from docking.i18n import _
from docking.log import get_logger
from docking.ui.display import clamp_popup, window_screen_position
from docking.ui.geometry import DockGeometryFrame

log = get_logger(name="tooltip")


def parse_timestamp(timestamp: dt.datetime | str | None) -> dt.datetime | None:
    """Parse an aware UTC/local timestamp into a timezone-aware datetime."""
    if timestamp is None:
        return None
    if isinstance(timestamp, dt.datetime):
        parsed = timestamp
    else:
        text = str(timestamp).strip()
        if not text:
            return None
        try:
            parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


def _format_relative_interval(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return (
            _("1 second")
            if seconds == 1
            else _("{count} seconds").format(count=seconds)
        )
    minutes = seconds // 60
    if minutes < 60:
        return (
            _("1 minute")
            if minutes == 1
            else _("{count} minutes").format(count=minutes)
        )
    hours = minutes // 60
    if hours < 24:
        return _("1 hour") if hours == 1 else _("{count} hours").format(count=hours)
    days = hours // 24
    return _("1 day") if days == 1 else _("{count} days").format(count=days)


def relative_time_label(
    timestamp: dt.datetime | str | None,
    *,
    now: dt.datetime | None = None,
) -> str:
    """Return a human relative age such as "5 minutes ago"."""
    parsed = parse_timestamp(timestamp)
    if parsed is None:
        return ""
    reference = now or dt.datetime.now(dt.timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=dt.timezone.utc)
    elapsed = reference.astimezone(dt.timezone.utc) - parsed.astimezone(dt.timezone.utc)
    elapsed_seconds = max(0, int(elapsed.total_seconds()))
    if elapsed_seconds == 0:
        return _("just now")
    return _("{age} ago").format(age=_format_relative_interval(elapsed_seconds))


if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.core.theme import Theme
    from docking.platform.model import DockModel


TOOLTIP_BASE_GAP = 10  # base gap between icon and tooltip
TOOLTIP_CORNER_RADIUS_PX = 6
TOOLTIP_CONTENT_MARGIN_PX = 6
TOOLTIP_BACKGROUND_ALPHA = 0.85
TOOLTIP_BOUNCE_HEADROOM_FACTOR = 0.5


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
    if pos == Position.TOP:
        return int(anchor_x - tooltip_w / 2), int(anchor_y + gap)
    if pos == Position.LEFT:
        return int(anchor_x + gap), int(anchor_y - tooltip_h / 2)
    # RIGHT
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
        self._pending_show_source: int = 0

    def set_theme(self, theme: Theme) -> None:
        """Update the theme used for tooltip spacing."""
        self._theme = theme

    def update(
        self,
        item: DockItem | None,
        geometry: DockGeometryFrame | None = None,
    ) -> None:
        """Show or reposition tooltip for the hovered icon.

        When item is None (cursor in gap between icons), keeps the last
        tooltip visible to avoid flicker. The dock's _on_leave hides it
        when the mouse actually exits the dock.
        """
        if not self._config.tooltips_enabled:
            self.hide()
            return

        if not item or not item.name:
            return

        if geometry is None:
            return

        # Build the display text, including recent-app context when applicable.
        display_text = item.name
        if item.is_recent and item.last_closed > 0:
            closed_dt = dt.datetime.fromtimestamp(item.last_closed, tz=dt.timezone.utc)
            rel = relative_time_label(closed_dt)
            if rel:
                display_text = f"{item.name}\n{rel}"

        # Check if content needs rebuilding (expensive: show_all triggers
        # crossing events) vs just repositioning (cheap: move only).
        content_changed = not (
            item is self._last_item and display_text == self._last_name
        )
        if content_changed:
            log.debug(f"content changed: {display_text}")

        item_geometry = geometry.geometry_for_item(item)
        if item_geometry is None:
            self.hide()
            return
        pos = self._config.pos
        window_pos = window_screen_position(self._window)
        anchor_x = window_pos.x + item_geometry.anchor_x
        anchor_y = window_pos.y + item_geometry.anchor_y

        if not content_changed:
            self._cancel_pending_show()
            self._show_tooltip(
                text=display_text,
                pos=pos,
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                content_changed=False,
            )
            return

        # Content rebuilds are the expensive branch. Coalesce them through an
        # idle callback so rapid hover churn across adjacent items only shows
        # the final stable item instead of briefly flashing stale tooltips.
        widget = item.tooltip_builder() if item.tooltip_builder else None
        self._schedule_show(
            item=item,
            text=display_text,
            pos=pos,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            widget=widget,
        )

    def _schedule_show(
        self,
        *,
        item: DockItem,
        text: str,
        pos: Position,
        anchor_x: float,
        anchor_y: float,
        widget: Gtk.Widget | None,
    ) -> None:
        self._cancel_pending_show()

        def run() -> bool:
            self._pending_show_source = 0
            self._last_item = item
            self._last_name = text
            self._show_tooltip(
                text=text,
                pos=pos,
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                widget=widget,
                content_changed=True,
            )
            return False

        self._pending_show_source = GLib.idle_add(run)

    def _cancel_pending_show(self) -> None:
        if self._pending_show_source:
            GLib.source_remove(self._pending_show_source)
            self._pending_show_source = 0

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
            try:
                self._tooltip_window.set_transient_for(self._window)
                self._tooltip_window.set_attached_to(self._window)
            except TypeError:
                pass
            self._tooltip_window.set_decorated(False)
            self._tooltip_window.set_skip_taskbar_hint(True)
            self._tooltip_window.set_resizable(False)
            self._tooltip_window.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
            self._tooltip_window.set_accept_focus(False)
            self._tooltip_window.set_focus_on_map(False)
            self._tooltip_window.set_app_paintable(True)

            screen = self._tooltip_window.get_screen()
            visual = screen.get_rgba_visual()
            if visual:
                self._tooltip_window.set_visual(visual)

            def on_draw(widget, cr):
                alloc = widget.get_allocation()
                radius = TOOLTIP_CORNER_RADIUS_PX
                w, h = alloc.width, alloc.height
                cr.new_sub_path()
                cr.arc(w - radius, radius, radius, -math.pi / 2, 0)
                cr.arc(w - radius, h - radius, radius, 0, math.pi / 2)
                cr.arc(radius, h - radius, radius, math.pi / 2, math.pi)
                cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
                cr.close_path()
                cr.set_source_rgba(0, 0, 0, TOOLTIP_BACKGROUND_ALPHA)
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
            content.set_margin_start(TOOLTIP_CONTENT_MARGIN_PX)
            content.set_margin_end(TOOLTIP_CONTENT_MARGIN_PX)
            content.set_margin_top(TOOLTIP_CONTENT_MARGIN_PX)
            content.set_margin_bottom(TOOLTIP_CONTENT_MARGIN_PX)
            self._tooltip_window.add(content)
            # Realize child so get_preferred_size returns the new
            # content's dimensions, not the previous tooltip's.
            content.show_all()

        pref = self._tooltip_window.get_preferred_size()[1]
        tw = max(pref.width, 1)
        th = max(pref.height, 1)

        # Gap = base gap + half bounce headroom (icon only briefly reaches peak)
        bounce_px = self._config.icon_size * self._theme.launch_bounce_height
        gap = TOOLTIP_BASE_GAP + bounce_px * TOOLTIP_BOUNCE_HEADROOM_FACTOR
        tx, ty = compute_tooltip_position(
            pos=pos,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            tooltip_w=tw,
            tooltip_h=th,
            gap=gap,
        )

        # Clamp to screen (respects parent-relative vs screen-absolute coords)
        tooltip_pos = clamp_popup(self._tooltip_window, tx, ty, tw, th)
        tx, ty = tooltip_pos.x, tooltip_pos.y

        log.debug(
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
        self._cancel_pending_show()
        self._last_item = None
        self._last_name = ""
        if self._tooltip_window:
            self._tooltip_window.hide()
