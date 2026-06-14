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

"""GTK lifecycle glue for bookmarks applet."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.bookmarks import meta
from docking.applets.bookmarks.render import render_icon
from docking.applets.bookmarks.state import (
    Bookmark,
    bookmarks_from_prefs,
    prefs_from_bookmarks,
    tooltip_text,
    truncate_label,
)
from docking.applets.menu import menu_sections
from docking.applets.popup import add_cancel_ok_buttons, prepare_dialog_content
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="bookmarks"), applet_id=meta.id)

ADD_DIALOG_WIDTH_PX = 350
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12


class BookmarksApplet(Applet):
    """Pinned URL bookmarks that open in the default browser."""

    id = meta.id
    name = _("Bookmarks")
    icon_name = "user-bookmarks"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._bookmarks: list[Bookmark] = bookmarks_from_prefs(
            config.applet_prefs.get("bookmarks", {}) if config else None
        )
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, count=len(self._bookmarks))

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(bookmarks=self._bookmarks)

    def on_clicked(self) -> None:
        if not self._bookmarks:
            return
        self._open_url(url=self._bookmarks[0].url)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        primary: list[Gtk.MenuItem] = []

        for bookmark in self._bookmarks:
            item = Gtk.MenuItem(label=truncate_label(text=bookmark.name))
            item.connect(
                "activate",
                lambda _w, url=bookmark.url: self._open_url(url=url),
            )
            primary.append(item)

        add_item = Gtk.MenuItem(label=_("Add Bookmark..."))
        add_item.connect("activate", lambda _: self._show_add_dialog())

        remove_all = Gtk.MenuItem(label=_("Remove All"))
        remove_all.connect("activate", lambda _: self._remove_all())
        remove_all.set_sensitive(bool(self._bookmarks))

        return menu_sections(
            primary=primary,
            manage=[add_item],
            destructive=[remove_all],
            gtk=Gtk,
        )

    def _open_url(self, url: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as exc:
            log.bind(action="open_url").warning(
                f"Failed to open URL: {exc}",
            )

    def _show_add_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Add Bookmark"),
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        add_cancel_ok_buttons(dialog=dialog)
        box = prepare_dialog_content(
            dialog=dialog,
            width=ADD_DIALOG_WIDTH_PX,
            spacing=DIALOG_CONTENT_SPACING_PX,
            margin=DIALOG_HORIZONTAL_MARGIN_PX,
            default_response=Gtk.ResponseType.OK,
        )

        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text(_("Bookmark name"))
        name_entry.set_activates_default(True)
        box.pack_start(name_entry, False, False, 0)

        url_entry = Gtk.Entry()
        url_entry.set_placeholder_text(_("https://..."))
        url_entry.set_activates_default(True)
        box.pack_start(url_entry, False, False, 0)

        dialog.show_all()
        name_entry.grab_focus()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            name = name_entry.get_text().strip()
            url = url_entry.get_text().strip()
            if name and url:
                self._bookmarks.append(Bookmark(name=name, url=url))
                self._save_prefs()
                self.present()
        dialog.destroy()

    def _remove_all(self) -> None:
        self._bookmarks.clear()
        self._save_prefs()
        self.present()

    def _save_prefs(self) -> None:
        self.save_prefs(prefs=prefs_from_bookmarks(bookmarks=self._bookmarks))
