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

"""Temporary dock-anchored New Year greeting popup.

The trigger semantics intentionally follow Cairo-Dock for reference.
I have fond memories of my time using and contributing to it, and this
feature is a small homage to all their influence and work on the dock bar
ecosystem:

- evaluate only after startup succeeds,
- greet once per year,
- only during the first 15 days of January,
- do not greet on the first-ever launch.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.core.greeting import consume_new_year_greeting
from docking.core.position import Position, is_horizontal
from docking.i18n import _
from docking.log import get_logger
from docking.ui.display import clamp_popup, window_screen_position
from docking.ui.tooltip import compute_tooltip_position

log = get_logger("new_year")

NEW_YEAR_POPUP_ID = "new-year"
NEW_YEAR_POPUP_PRIORITY = 10
STARTUP_GREETING_DELAY_S = 5
NEW_YEAR_GREETING_DURATION_S = 15
GREETING_GAP_PX = 16
GREETING_CONTENT_SPACING_PX = 12
GREETING_MARGIN_PX = 12
GREETING_CORNER_RADIUS_PX = 10
GREETING_TIP_WIDTH_PX = 22
GREETING_TIP_HEIGHT_PX = 12
GREETING_BACKGROUND_RGBA = (0.11, 0.11, 0.11, 0.96)
GREETING_BORDER_RGBA = (1.0, 1.0, 1.0, 0.14)


class NewYearGreetingController:
    """Schedules and displays the dock's annual New Year greeting."""

    source_id = NEW_YEAR_POPUP_ID
    priority = NEW_YEAR_POPUP_PRIORITY
    max_wait_seconds: int | None = None

    def __init__(
        self,
        *,
        window: Gtk.Window,
        state_path: Path | str | None = None,
        now_fn: Callable[[], datetime] | None = None,
    ) -> None:
        self._window = window
        self._state_path = Path(state_path) if state_path else None
        self._now_fn = now_fn or datetime.now
        self._start_source_id: int = 0
        self._hide_source_id: int = 0
        self._popup: Gtk.Window | None = None
        self._pending_year: int | None = None
        self._request_show: Callable[[str], None] | None = None
        self._visibility_changed: Callable[[str, bool], None] | None = None

    def start(
        self,
        request_show: Callable[[str], None] | None = None,
        visibility_changed: Callable[[str, bool], None] | None = None,
    ) -> None:
        """Start the delayed startup greeting check once per process."""
        if self._start_source_id:
            return
        self._request_show = request_show
        self._visibility_changed = visibility_changed
        log.debug(
            "Scheduling New Year greeting check in %ss",
            STARTUP_GREETING_DELAY_S,
        )
        self._start_source_id = GLib.timeout_add_seconds(
            STARTUP_GREETING_DELAY_S,
            self._on_startup_complete,
        )

    def stop(self) -> None:
        """Cancel any pending timers and close the popup."""
        if self._start_source_id:
            GLib.source_remove(self._start_source_id)
            self._start_source_id = 0
        if self._hide_source_id:
            GLib.source_remove(self._hide_source_id)
            self._hide_source_id = 0
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None
        self._notify_visible(False)

    def _on_startup_complete(self) -> bool:
        self._start_source_id = 0
        now = self._now_fn()
        year = consume_new_year_greeting(
            path=self._state_path,
            now=now,
        )
        log.debug("New Year greeting evaluation at %s returned %r", now, year)
        if year is not None:
            self._pending_year = year
            if self._request_show is not None:
                self._request_show(self.source_id)
            else:
                self.show_pending()
        return False

    def show_pending(self) -> bool:
        """Show a pending New Year greeting if one exists."""
        if self._pending_year is None:
            return False
        year = self._pending_year
        self._pending_year = None
        return self._show_popup(year=year)

    def _show_popup(self, *, year: int) -> bool:
        if not self._window.get_realized():
            log.debug("Skipping New Year greeting because dock window is not realized")
            return False

        if self._popup is None:
            popup = Gtk.Window(type=Gtk.WindowType.POPUP)
            popup.set_decorated(False)
            popup.set_skip_taskbar_hint(True)
            popup.set_resizable(False)
            popup.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
            popup.set_app_paintable(True)
            popup.set_transient_for(self._window)
            popup.connect("button-press-event", self._on_popup_button_press)
            screen = popup.get_screen()
            visual = screen.get_rgba_visual()
            if visual is not None:
                popup.set_visual(visual)
            popup.connect("draw", self._on_popup_draw)
            popup.connect("destroy", self._on_popup_destroy)
            self._popup = popup
        else:
            child = self._popup.get_child()
            if child is not None:
                self._popup.remove(child)

        log.debug("Showing New Year greeting popup for year %s", year)
        self._popup.add(self._build_popup_content(year=year))
        self._popup.show_all()
        self._notify_visible(True)
        self._position_popup()

        if self._hide_source_id:
            GLib.source_remove(self._hide_source_id)
        self._hide_source_id = GLib.timeout_add_seconds(
            NEW_YEAR_GREETING_DURATION_S,
            self._hide_popup,
        )
        return True

    def _build_popup_content(self, *, year: int) -> Gtk.Widget:
        pos = self._window.config.pos
        box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=GREETING_CONTENT_SPACING_PX,
        )
        box.set_margin_start(
            GREETING_MARGIN_PX + (GREETING_TIP_HEIGHT_PX if pos == Position.LEFT else 0)
        )
        box.set_margin_end(
            GREETING_MARGIN_PX
            + (GREETING_TIP_HEIGHT_PX if pos == Position.RIGHT else 0)
        )
        box.set_margin_top(
            GREETING_MARGIN_PX + (GREETING_TIP_HEIGHT_PX if pos == Position.TOP else 0)
        )
        box.set_margin_bottom(
            GREETING_MARGIN_PX
            + (GREETING_TIP_HEIGHT_PX if pos == Position.BOTTOM else 0)
        )

        image = Gtk.Image.new_from_icon_name("face-smile", Gtk.IconSize.DIALOG)
        image.set_valign(Gtk.Align.CENTER)
        box.pack_start(image, False, False, 0)

        label = Gtk.Label(label=_("Happy new year {year} !!!").format(year=year))
        label.set_xalign(0.0)
        label.set_yalign(0.5)
        label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        box.pack_start(label, False, False, 0)
        return box

    def _position_popup(self) -> None:
        if self._popup is None:
            return

        window_pos = window_screen_position(self._window)
        win_x, win_y = window_pos.x, window_pos.y
        win_w, win_h = self._window.get_size()
        pref = self._popup.get_preferred_size()[1]
        popup_w = max(pref.width, 1)
        popup_h = max(pref.height, 1)
        pos = self._window.config.pos

        if is_horizontal(pos):
            anchor_x = win_x + win_w / 2
            anchor_y = win_y if pos.value == "bottom" else win_y + win_h
        else:
            anchor_x = win_x + win_w if pos.value == "left" else win_x
            anchor_y = win_y + win_h / 2

        popup_x, popup_y = compute_tooltip_position(
            pos=pos,
            anchor_x=anchor_x,
            anchor_y=anchor_y,
            tooltip_w=popup_w,
            tooltip_h=popup_h,
            gap=GREETING_GAP_PX,
        )

        clamped = clamp_popup(self._popup, popup_x, popup_y, popup_w, popup_h)
        log.debug(
            "Positioned New Year greeting popup at (%s, %s) "
            "size=%sx%s dock=(%s,%s %sx%s pos=%s)",
            clamped.x,
            clamped.y,
            popup_w,
            popup_h,
            win_x,
            win_y,
            win_w,
            win_h,
            pos.value,
        )
        self._popup.move(clamped.x, clamped.y)

    def _on_popup_draw(self, widget: Gtk.Widget, cr) -> bool:
        alloc = widget.get_allocation()
        pos = self._window.config.pos
        width = alloc.width
        height = alloc.height
        radius = GREETING_CORNER_RADIUS_PX
        tip_w = GREETING_TIP_WIDTH_PX
        tip_h = GREETING_TIP_HEIGHT_PX

        body_x = 0.0
        body_y = 0.0
        body_w = float(width)
        body_h = float(height)
        if pos == Position.BOTTOM:
            body_h -= tip_h
        elif pos == Position.TOP:
            body_y += tip_h
            body_h -= tip_h
        elif pos == Position.LEFT:
            body_x += tip_h
            body_w -= tip_h
        else:
            body_w -= tip_h

        tip_half = tip_w / 2
        if pos in (Position.BOTTOM, Position.TOP):
            tip_center = body_x + body_w / 2
        else:
            tip_center = body_y + body_h / 2

        cr.new_path()
        if pos == Position.BOTTOM:
            cr.move_to(tip_center - tip_half, body_y + body_h)
            cr.line_to(tip_center, body_y + body_h + tip_h)
            cr.line_to(tip_center + tip_half, body_y + body_h)
        elif pos == Position.TOP:
            cr.move_to(tip_center - tip_half, body_y)
            cr.line_to(tip_center, body_y - tip_h)
            cr.line_to(tip_center + tip_half, body_y)
        elif pos == Position.LEFT:
            cr.move_to(body_x, tip_center - tip_half)
            cr.line_to(body_x - tip_h, tip_center)
            cr.line_to(body_x, tip_center + tip_half)
        else:
            cr.move_to(body_x + body_w, tip_center - tip_half)
            cr.line_to(body_x + body_w + tip_h, tip_center)
            cr.line_to(body_x + body_w, tip_center + tip_half)
        cr.close_path()

        self._append_rounded_rect(
            cr,
            x=body_x,
            y=body_y,
            width=body_w,
            height=body_h,
            radius=radius,
        )
        cr.set_source_rgba(*GREETING_BACKGROUND_RGBA)
        cr.fill_preserve()
        cr.set_source_rgba(*GREETING_BORDER_RGBA)
        cr.set_line_width(1.0)
        cr.stroke()
        return False

    def _append_rounded_rect(
        self,
        cr,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
        radius: float,
    ) -> None:
        radius = min(radius, width / 2, height / 2)
        cr.new_sub_path()
        cr.arc(
            x + width - radius,
            y + radius,
            radius,
            -3.141592653589793 / 2,
            0,
        )
        cr.arc(
            x + width - radius,
            y + height - radius,
            radius,
            0,
            3.141592653589793 / 2,
        )
        cr.arc(
            x + radius,
            y + height - radius,
            radius,
            3.141592653589793 / 2,
            3.141592653589793,
        )
        cr.arc(
            x + radius,
            y + radius,
            radius,
            3.141592653589793,
            3.141592653589793 * 3 / 2,
        )
        cr.close_path()

    def _hide_popup(self) -> bool:
        self._hide_source_id = 0
        if self._popup is not None:
            self._popup.hide()
        self._notify_visible(False)
        return False

    def _on_popup_destroy(self, _popup: Gtk.Window) -> None:
        self._notify_visible(False)

    def _on_popup_button_press(
        self,
        _widget: Gtk.Widget,
        _event: Gdk.EventButton,
    ) -> bool:
        if self._hide_source_id:
            GLib.source_remove(self._hide_source_id)
            self._hide_source_id = 0
        self._hide_popup()
        return True

    def _notify_visible(self, visible: bool) -> None:
        if self._visibility_changed is not None:
            self._visibility_changed(self.source_id, visible)
