"""GTK lifecycle glue for Calendar applet."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.calendar.render import render_icon
from docking.applets.calendar.state import snapshot_from
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.ui.runtime import get_pointer_position

if TYPE_CHECKING:
    from docking.core.config import Config

CALENDAR_TICK_INTERVAL_S = 30
CALENDAR_POPUP_CORNER_RADIUS_PX = 8
CALENDAR_POPUP_PADDING_PX = 8
CALENDAR_POPUP_CURSOR_GAP_PX = 20


class CalendarApplet(Applet):
    """Displays today's date as a dock icon with calendar popup on click."""

    id = AppletId.CALENDAR
    name = _("Calendar")
    icon_name = "office-calendar"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._last_day: int = -1
        self._tooltip_text: str = _("Calendar")
        self._popup: Gtk.Window | None = None
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        snapshot = snapshot_from()
        self._tooltip_text = snapshot.tooltip
        self._last_day = snapshot.day
        return render_icon(size=size, snapshot=snapshot)

    def refresh_tooltip(self) -> None:
        self.item.name = self._tooltip_text

    def on_clicked(self) -> None:
        if self._popup and self._popup.get_visible():
            self._popup.hide()
            return
        self._show_popup()

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(CALENDAR_TICK_INTERVAL_S, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._popup:
            self._popup.destroy()
            self._popup = None
        super().stop()

    def _tick(self) -> bool:
        snapshot = snapshot_from()
        self._tooltip_text = snapshot.tooltip
        if snapshot.day != self._last_day:
            self.present()
        else:
            self.refresh_tooltip()
        return True

    def _show_popup(self) -> None:
        if self._popup is None:
            self._popup = Gtk.Window(type=Gtk.WindowType.POPUP)
            self._popup.set_decorated(False)
            self._popup.set_skip_taskbar_hint(True)
            self._popup.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
            self._popup.set_app_paintable(True)

            screen = self._popup.get_screen()
            visual = screen.get_rgba_visual()
            if visual:
                self._popup.set_visual(visual)

            def on_draw(widget: Gtk.Widget, cr: cairo.Context) -> bool:
                alloc = widget.get_allocation()
                radius = CALENDAR_POPUP_CORNER_RADIUS_PX
                width, height = alloc.width, alloc.height
                cr.new_sub_path()
                cr.arc(width - radius, radius, radius, -math.pi / 2, 0)
                cr.arc(width - radius, height - radius, radius, 0, math.pi / 2)
                cr.arc(radius, height - radius, radius, math.pi / 2, math.pi)
                cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
                cr.close_path()
                cr.set_source_rgba(0.12, 0.12, 0.12, 0.92)
                cr.fill()
                return False

            self._popup.connect("draw", on_draw)

        child = self._popup.get_child()
        if child:
            self._popup.remove(child)

        calendar = Gtk.Calendar()
        calendar.set_margin_start(CALENDAR_POPUP_PADDING_PX)
        calendar.set_margin_end(CALENDAR_POPUP_PADDING_PX)
        calendar.set_margin_top(CALENDAR_POPUP_PADDING_PX)
        calendar.set_margin_bottom(CALENDAR_POPUP_PADDING_PX)
        self._popup.add(calendar)

        self._popup.show_all()

        # Position near mouse
        display = Gdk.Display.get_default()
        pos = get_pointer_position(display)
        mouse_x, mouse_y = pos if pos else (0, 0)

        pref = self._popup.get_preferred_size()[1]
        popup_w = max(pref.width, 1)
        popup_h = max(pref.height, 1)

        screen = self._popup.get_screen()
        screen_w = screen.get_width()
        screen_h = screen.get_height()

        popup_x = max(0, min(int(mouse_x - popup_w / 2), screen_w - popup_w))
        popup_y = max(
            0,
            min(
                int(mouse_y - popup_h - CALENDAR_POPUP_CURSOR_GAP_PX),
                screen_h - popup_h,
            ),
        )

        self._popup.move(popup_x, popup_y)
