"""GTK lifecycle glue for bookmarks applet."""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.bookmarks.render import render_icon
from docking.applets.bookmarks.state import (
    Bookmark,
    bookmarks_from_prefs,
    prefs_from_bookmarks,
    tooltip_text,
    truncate_label,
)
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="bookmarks"), applet_id=str(AppletId.BOOKMARKS))

ADD_DIALOG_WIDTH_PX = 350
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12
DIALOG_VERTICAL_MARGIN_PX = 8


class BookmarksApplet(Applet):
    """Pinned URL bookmarks that open in the default browser."""

    id = AppletId.BOOKMARKS
    name = _("Bookmarks")
    icon_name = "user-bookmarks"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._bookmarks: list[Bookmark] = bookmarks_from_prefs(
            config.applet_prefs.get("bookmarks", {}) if config else None
        )
        super().__init__(icon_size=icon_size, config=config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, count=len(self._bookmarks))

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(bookmarks=self._bookmarks)

    def on_clicked(self) -> None:
        if not self._bookmarks:
            return
        self._open_url(url=self._bookmarks[0].url)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        for bookmark in self._bookmarks:
            item = Gtk.MenuItem(label=truncate_label(text=bookmark.name))
            item.connect(
                "activate",
                lambda _w, url=bookmark.url: self._open_url(url=url),
            )
            items.append(item)

        if self._bookmarks:
            items.append(Gtk.SeparatorMenuItem())

        add_item = Gtk.MenuItem(label=_("Add Bookmark..."))
        add_item.connect("activate", lambda _: self._show_add_dialog())
        items.append(add_item)

        remove_all = Gtk.MenuItem(label=_("Remove All"))
        remove_all.connect("activate", lambda _: self._remove_all())
        remove_all.set_sensitive(bool(self._bookmarks))
        items.append(remove_all)

        return items

    def _open_url(self, url: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as exc:
            _log.bind(action="open_url").warning(
                f"Failed to open URL: {exc}",
            )

    def _show_add_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Add Bookmark"),
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL,
            Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK,
            Gtk.ResponseType.OK,
        )
        dialog.set_default_size(ADD_DIALOG_WIDTH_PX, -1)
        dialog.set_position(Gtk.WindowPosition.MOUSE)

        box = dialog.get_content_area()
        box.set_spacing(DIALOG_CONTENT_SPACING_PX)
        box.set_margin_start(DIALOG_HORIZONTAL_MARGIN_PX)
        box.set_margin_end(DIALOG_HORIZONTAL_MARGIN_PX)
        box.set_margin_top(DIALOG_VERTICAL_MARGIN_PX)
        box.set_margin_bottom(DIALOG_VERTICAL_MARGIN_PX)

        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text(_("Bookmark name"))
        box.pack_start(name_entry, False, False, 0)

        url_entry = Gtk.Entry()
        url_entry.set_placeholder_text(_("https://..."))
        box.pack_start(url_entry, False, False, 0)

        dialog.show_all()

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            name = name_entry.get_text().strip()
            url = url_entry.get_text().strip()
            if name and url:
                self._bookmarks.append(Bookmark(name=name, url=url))
                self._save_prefs()
                self.refresh_presentation()
        dialog.destroy()

    def _remove_all(self) -> None:
        self._bookmarks.clear()
        self._save_prefs()
        self.refresh_presentation()

    def _save_prefs(self) -> None:
        self.save_prefs(prefs=prefs_from_bookmarks(bookmarks=self._bookmarks))
