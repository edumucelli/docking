"""Recent Files applet behavior and GTK wiring."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import render_icon
from .state import MAX_ENTRIES, RecentEntry, tooltip_text, truncate_name

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="recentfiles"), applet_id=str(AppletId.RECENTFILES))


class RecentFilesApplet(Applet):
    """Shows recently opened files; click opens the most recent."""

    id = AppletId.RECENTFILES
    name = _("Recent Files")
    icon_name = "document-open-recent"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._entries: list[RecentEntry] = []
        self._signal_id: int | None = None
        self._refresh_entries()
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, has_files=bool(self._entries))

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(entries=self._entries)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        manager = Gtk.RecentManager.get_default()
        self._signal_id = manager.connect("changed", self._on_changed)

    def stop(self) -> None:
        if self._signal_id is not None:
            manager = Gtk.RecentManager.get_default()
            manager.disconnect(self._signal_id)
            self._signal_id = None
        super().stop()

    def on_clicked(self) -> None:
        """Open the most recent file."""
        if not self._entries:
            return
        uri = self._entries[0].uri
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            _log.bind(action="open_recent").warning("Failed to open %s: %s", uri, exc)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        for entry in self._entries:
            mi = Gtk.MenuItem(label=truncate_name(text=entry.name))
            uri = entry.uri
            mi.connect("activate", lambda _, u=uri: self._open_uri(uri=u))
            items.append(mi)

        if self._entries:
            items.append(Gtk.SeparatorMenuItem())

        clear_item = Gtk.MenuItem(label=_("Clear Recent Files"))
        clear_item.set_sensitive(bool(self._entries))
        clear_item.connect("activate", lambda _: self._clear_recent())
        items.append(clear_item)

        return items

    def _refresh_entries(self) -> None:
        """Read from Gtk.RecentManager, sort by modified time descending."""
        manager = Gtk.RecentManager.get_default()
        raw_items = manager.get_items()
        existing = [it for it in raw_items if it.exists()]
        existing.sort(key=lambda it: it.get_modified(), reverse=True)
        self._entries = [
            RecentEntry(name=it.get_display_name(), uri=it.get_uri())
            for it in existing[:MAX_ENTRIES]
        ]

    def _on_changed(self, *_args: object) -> None:
        self._refresh_entries()
        self.refresh_presentation()

    def _open_uri(self, *, uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            _log.bind(action="open_recent").warning("Failed to open %s: %s", uri, exc)

    def _clear_recent(self) -> None:
        try:
            Gtk.RecentManager.get_default().purge_items()
        except GLib.Error as exc:
            _log.bind(action="clear_recent").warning(
                "Failed to clear recent files: %s", exc
            )
