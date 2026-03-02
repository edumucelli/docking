"""Calendar applet -- shows today's date, click opens calendar popup."""

from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING, Callable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="calendar")


class CalendarApplet(Applet):
    """Displays today's date as a dock icon with calendar popup on click.

    Icon shows the day number on a calendar-style background.
    Tooltip shows the full date. Click toggles a GtkCalendar popup.
    """

    id = AppletId.CALENDAR
    name = "Calendar"
    icon_name = "office-calendar"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._last_day: int = -1
        self._popup: Gtk.Window | None = None
        super().__init__(icon_size, config)
        self.item.name = time.strftime("%a, %b %-d %H:%M", time.localtime())

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        now = time.localtime()
        day = now.tm_mday

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_calendar_icon(
            cr=cr, size=size, day=day, weekday=time.strftime("%a", now)
        )

        if hasattr(self, "item"):
            self.item.name = time.strftime("%a, %b %-d %H:%M", now)

        self._last_day = day
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        if self._popup and self._popup.get_visible():
            self._popup.hide()
            return
        self._show_popup()

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        self._timer_id = GLib.timeout_add_seconds(30, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._popup:
            self._popup.destroy()
            self._popup = None
        super().stop()

    def _tick(self) -> bool:
        now = time.localtime()
        self.item.name = time.strftime("%a, %b %-d %H:%M", now)
        if now.tm_mday != self._last_day:
            self.refresh_icon()
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
                radius = 8
                w, h = alloc.width, alloc.height
                cr.new_sub_path()
                cr.arc(w - radius, radius, radius, -math.pi / 2, 0)
                cr.arc(w - radius, h - radius, radius, 0, math.pi / 2)
                cr.arc(radius, h - radius, radius, math.pi / 2, math.pi)
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
        calendar.set_margin_start(8)
        calendar.set_margin_end(8)
        calendar.set_margin_top(8)
        calendar.set_margin_bottom(8)
        self._popup.add(calendar)

        self._popup.show_all()

        # Position near mouse
        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        pointer = seat.get_pointer()
        _, mouse_x, mouse_y = pointer.get_position()

        pref = self._popup.get_preferred_size()[1]
        popup_w = max(pref.width, 1)
        popup_h = max(pref.height, 1)

        screen = self._popup.get_screen()
        screen_w = screen.get_width()
        screen_h = screen.get_height()

        popup_x = max(0, min(int(mouse_x - popup_w / 2), screen_w - popup_w))
        popup_y = max(0, min(int(mouse_y - popup_h - 20), screen_h - popup_h))

        self._popup.move(popup_x, popup_y)


def _render_calendar_icon(cr: cairo.Context, size: int, day: int, weekday: str) -> None:
    """Draw a calendar page icon with day number and weekday abbreviation."""
    margin = size * 0.08
    body_x = margin
    body_y = margin
    body_w = size - 2 * margin
    body_h = size - 2 * margin
    radius = size * 0.1
    header_h = body_h * 0.3

    # Body (white rounded rect)
    cr.new_sub_path()
    cr.arc(body_x + body_w - radius, body_y + radius, radius, -math.pi / 2, 0)
    cr.arc(body_x + body_w - radius, body_y + body_h - radius, radius, 0, math.pi / 2)
    cr.arc(body_x + radius, body_y + body_h - radius, radius, math.pi / 2, math.pi)
    cr.arc(body_x + radius, body_y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgba(0.95, 0.95, 0.95, 1)
    cr.fill()

    # Red header bar
    cr.new_sub_path()
    cr.arc(body_x + body_w - radius, body_y + radius, radius, -math.pi / 2, 0)
    cr.line_to(body_x + body_w, body_y + header_h)
    cr.line_to(body_x, body_y + header_h)
    cr.arc(body_x + radius, body_y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()
    cr.set_source_rgba(0.85, 0.18, 0.18, 1)
    cr.fill()

    # Weekday text in header
    weekday_font_size = max(1, int(header_h * 0.55))
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(
        Pango.FontDescription(f"Sans Bold {weekday_font_size}px")
    )
    layout.set_text(weekday.upper(), -1)
    _ink, logical = layout.get_pixel_extents()
    tx = body_x + (body_w - logical.width) / 2 - logical.x
    ty = body_y + (header_h - logical.height) / 2 - logical.y
    cr.move_to(tx, ty)
    cr.set_source_rgba(1, 1, 1, 1)
    PangoCairo.show_layout(cr, layout)

    # Day number in body
    day_area_h = body_h - header_h
    day_font_size = max(1, int(day_area_h * 0.65))
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription(f"Sans Bold {day_font_size}px"))
    layout.set_text(str(day), -1)
    _ink, logical = layout.get_pixel_extents()
    tx = body_x + (body_w - logical.width) / 2 - logical.x
    ty = body_y + header_h + (day_area_h - logical.height) / 2 - logical.y
    cr.move_to(tx, ty)
    cr.set_source_rgba(0.15, 0.15, 0.15, 1)
    PangoCairo.show_layout(cr, layout)
