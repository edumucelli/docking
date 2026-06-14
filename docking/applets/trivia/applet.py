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

"""Random Trivia applet behavior and GTK wiring."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.trivia import meta
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import draw_trivia_icon
from .state import (
    DEFAULT_FETCH_LIMIT,
    TriviaEntry,
    answer_entry,
    fallback_trivia,
    fetch_trivia,
    format_trivia,
)

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="trivia"), applet_id=meta.id)


class TriviaApplet(Applet):
    """Quick trivia applet backed by Open Trivia DB with local fallback."""

    id = meta.id
    name = _("Random Trivia")
    icon_name = "dialog-question"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._entries: list[TriviaEntry] = []
        self._index = -1
        self._current: TriviaEntry | None = None
        self._loading = False
        super().__init__(icon_size, config)
        self._entries = fallback_trivia()
        self._advance_entry()
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        draw_trivia_icon(cr=cr, size=size, entry=self._current)
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        if self._advance_entry():
            self.present()
            if self._index >= len(self._entries) - 1:
                self._fetch_async(show_first=False)
            return
        self._current = None
        self._fetch_async(show_first=True)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = [disabled_menu_item("Open Trivia DB", gtk=Gtk)]

        primary: list[Gtk.MenuItem] = []
        if self._current is not None:
            status.append(
                disabled_menu_item(
                    _("{category} / {difficulty}").format(
                        category=self._current.category,
                        difficulty=self._current.difficulty.title(),
                    ),
                    gtk=Gtk,
                )
            )

            if not self._current.selected_answer:
                for answer in self._current.answers:
                    answer_item = Gtk.MenuItem(label=answer)
                    answer_item.connect(
                        "activate",
                        lambda _w, a=answer: self._select_answer(a),
                    )
                    primary.append(answer_item)
            else:
                status.append(
                    disabled_menu_item(
                        _("Correct answer: {answer}").format(
                            answer=self._current.correct_answer
                        ),
                        gtk=Gtk,
                    )
                )
                if self._current.selected_answer != self._current.correct_answer:
                    status.append(
                        disabled_menu_item(
                            _("Your answer: {answer}").format(
                                answer=self._current.selected_answer
                            ),
                            gtk=Gtk,
                        )
                    )

        next_item = Gtk.MenuItem(label=_("Next Trivia"))
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

    def _select_answer(self, answer: str) -> None:
        if self._current is None or self._current.selected_answer:
            return
        self._current = answer_entry(self._current, answer)
        self.present()

    def _refresh_from_web(self) -> None:
        self._fetch_async(show_first=True)

    def _fetch_async(self, show_first: bool) -> None:
        if self._loading:
            return
        self._loading = True
        if show_first:
            self.present()

        def worker() -> None:
            entries = fetch_trivia(limit=DEFAULT_FETCH_LIMIT)
            GLib.idle_add(self._on_fetch_result, entries, show_first)

        threading.Thread(target=worker, daemon=True).start()

    def _on_fetch_result(
        self,
        entries: list[TriviaEntry],
        show_first: bool,
    ) -> bool:
        self._loading = False

        if entries:
            self._entries = entries
            self._index = -1
            if show_first:
                self._current = None
                self._advance_entry()
        elif self._current is None:
            self._entries = fallback_trivia()
            self._index = -1
            self._advance_entry()

        self.refresh_tooltip()
        self.present()
        return False

    def _advance_entry(self) -> bool:
        nxt = self._index + 1
        if nxt >= len(self._entries):
            return False
        self._index = nxt
        self._current = self._entries[nxt]
        self.refresh_tooltip()
        return True

    def refresh_tooltip(self) -> None:
        if self._loading and self._current is None:
            self.item.name = _("Trivia: loading...")
            return
        if self._current is not None:
            self.item.name = format_trivia(self._current)
            return
        self.item.name = _("Random Trivia")
