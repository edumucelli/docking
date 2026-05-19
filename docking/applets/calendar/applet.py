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

"""GTK lifecycle glue for Calendar applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.calendar import meta
from docking.applets.calendar.render import render_icon
from docking.applets.calendar.state import snapshot_from
from docking.applets.popup import create_popup_window, show_wrapped_popup
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

CALENDAR_TICK_INTERVAL_S = 30
CALENDAR_POPUP_PADDING_PX = 8
CALENDAR_POPUP_CURSOR_GAP_PX = 20


class CalendarApplet(Applet):
    """Displays today's date as a dock icon with calendar popup on click."""

    id = meta.id
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
            self._popup = create_popup_window()

        calendar = Gtk.Calendar()
        calendar.set_margin_start(CALENDAR_POPUP_PADDING_PX)
        calendar.set_margin_end(CALENDAR_POPUP_PADDING_PX)
        calendar.set_margin_top(CALENDAR_POPUP_PADDING_PX)
        calendar.set_margin_bottom(CALENDAR_POPUP_PADDING_PX)
        show_wrapped_popup(
            window=self._popup,
            content=calendar,
            gap_px=CALENDAR_POPUP_CURSOR_GAP_PX,
        )
