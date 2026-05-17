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

"""GTK wiring for the Today in History applet."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.todayinhistory import meta
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import render_icon
from .state import (
    DEFAULT_FETCH_LIMIT,
    HistoryEvent,
    fallback_today_in_history,
    fetch_today_in_history,
    format_history_event,
)

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(
    get_logger(name="todayinhistory"),
    applet_id=meta.id,
)

DAY_CHANGE_POLL_INTERVAL_S = 60


class TodayInHistoryApplet(Applet):
    """Show notable historical events for the current local date."""

    id = meta.id
    name = _("Today in History")
    icon_name = "office-calendar"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._events: list[HistoryEvent] = []
        self._index = -1
        self._current: HistoryEvent | None = None
        self._loading = False
        self._loading_key = ""
        self._timer_id = 0
        self._current_month, self._current_day = self._current_date()
        super().__init__(icon_size, config)
        self._load_fallback_for(month=self._current_month, day=self._current_day)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._fetch_async(show_first=False)
        self._timer_id = GLib.timeout_add_seconds(
            DAY_CHANGE_POLL_INTERVAL_S,
            self._poll_day_change,
        )

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        if not self._events:
            self._current = None
            self._fetch_async(show_first=True)
            return
        self._advance_event()
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status = [disabled_menu_item(self._source_label(), gtk=Gtk)]

        primary: list[Gtk.MenuItem] = []
        if self._current is not None and self._current.article_url:
            open_item = Gtk.MenuItem(label=_("Open Article"))
            open_item.connect("activate", lambda _w: self._open_current_article())
            primary.append(open_item)

        next_item = Gtk.MenuItem(label=_("Next Event"))
        next_item.connect("activate", lambda _w: self.on_clicked())

        refresh_item = Gtk.MenuItem(label=_("Refresh Now"))
        refresh_item.connect("activate", lambda _w: self._refresh_from_web())

        return menu_sections(
            status=status,
            primary=primary,
            navigation=[next_item],
            refresh=[refresh_item],
            gtk=Gtk,
        )

    def refresh_tooltip(self) -> None:
        if self._loading and self._current is None:
            self.item.name = _("Today in History: loading...")
            return
        if self._current is not None:
            self.item.name = format_history_event(self._current)
            return
        self.item.name = _("Today in History")

    def _current_date(self) -> tuple[int, int]:
        now = time.localtime()
        return now.tm_mon, now.tm_mday

    def _date_key(self, *, month: int, day: int) -> str:
        return f"{month:02d}-{day:02d}"

    def _source_label(self) -> str:
        if self._current is not None:
            return self._current.source_label
        return _("Today in History")

    def _load_fallback_for(self, *, month: int, day: int) -> None:
        self._events = fallback_today_in_history(month=month, day=day)
        self._index = -1
        self._current = None
        if self._events:
            self._advance_event()

    def _sync_to_local_day(self) -> bool:
        month, day = self._current_date()
        if (month, day) == (self._current_month, self._current_day):
            return False
        self._current_month = month
        self._current_day = day
        # Any in-flight fetch for the previous day is now stale by definition.
        self._loading = False
        self._loading_key = ""
        self._load_fallback_for(month=month, day=day)
        return True

    def _advance_event(self) -> None:
        if not self._events:
            self._index = -1
            self._current = None
            return
        self._index = (self._index + 1) % len(self._events)
        self._current = self._events[self._index]
        self.refresh_tooltip()

    def _refresh_from_web(self) -> None:
        self._fetch_async(show_first=True)

    def _fetch_async(self, show_first: bool) -> None:
        self._sync_to_local_day()
        month = self._current_month
        day = self._current_day
        request_key = self._date_key(month=month, day=day)
        if self._loading and request_key == self._loading_key:
            return

        self._loading = True
        self._loading_key = request_key
        if show_first:
            self.present()

        def worker() -> None:
            entries = fetch_today_in_history(
                month=month,
                day=day,
                limit=DEFAULT_FETCH_LIMIT,
            )
            GLib.idle_add(self._on_fetch_result, month, day, entries, show_first)

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_result(
        self,
        month: int,
        day: int,
        entries: list[HistoryEvent],
        show_first: bool,
    ) -> bool:
        request_key = self._date_key(month=month, day=day)
        if request_key == self._loading_key:
            self._loading = False
            self._loading_key = ""

        current_key = self._date_key(month=self._current_month, day=self._current_day)
        if request_key != current_key:
            return False

        if entries:
            self._events = entries
            self._index = -1
            if show_first or self._current is None:
                self._current = None
                self._advance_event()
        elif self._current is None:
            self._events = fallback_today_in_history(month=month, day=day)
            self._index = -1
            self._current = None
            self._advance_event()

        self.refresh_tooltip()
        self.present()
        return False

    def _poll_day_change(self) -> bool:
        if not self._sync_to_local_day():
            return True

        self.present()
        self._fetch_async(show_first=False)
        return True

    def _open_current_article(self) -> None:
        if self._current is None or not self._current.article_url:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(self._current.article_url, None)
        except Exception as exc:
            log.bind(action="open_article").warning(
                "Failed to open %s: %s",
                self._current.article_url,
                exc,
            )
