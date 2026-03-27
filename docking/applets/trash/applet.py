"""Trash applet behavior and GTK wiring."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.trash import meta
from docking.i18n import _
from docking.log import get_logger, with_context

from .render import create_trash_icon, trash_tooltip
from .state import _count_trash_items

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="trash"), applet_id=meta.id)


class TrashApplet(Applet):
    """Shows trash state icon; click opens, menu allows emptying."""

    id = meta.id
    name = _("Trash")
    icon_name = "user-trash"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._item_count = _count_trash_items()
        self._monitor: Gio.FileMonitor | None = None
        super().__init__(icon_size, config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_trash_icon(size=size, item_count=self._item_count)

    def refresh_tooltip(self) -> None:
        self.item.name = trash_tooltip(item_count=self._item_count)

    def on_clicked(self) -> None:
        """Open trash folder in the default file manager."""
        try:
            Gio.AppInfo.launch_default_for_uri("trash:///", None)
        except GLib.Error as exc:
            _log.bind(action="open_trash").warning(f"Failed to open trash: {exc}")

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Return 'Open Trash' and 'Empty Trash' menu items."""
        items: list[Gtk.MenuItem] = []

        open_item = Gtk.MenuItem(label=_("Open Trash"))
        open_item.connect("activate", lambda _: self.on_clicked())
        items.append(open_item)

        empty_item = Gtk.MenuItem(label=_("Empty Trash"))
        empty_item.set_sensitive(self._item_count > 0)
        empty_item.connect("activate", lambda _: self._empty_trash())
        items.append(empty_item)

        return items

    def start(self, notify: Callable[[], None]) -> None:
        """Start Gio.FileMonitor on trash:/// for real-time icon updates."""
        super().start(notify)
        trash = Gio.File.new_for_uri("trash:///")
        try:
            self._monitor = trash.monitor(Gio.FileMonitorFlags.NONE, None)
            self._monitor.connect("changed", self._on_trash_changed)
        except GLib.Error as exc:
            _log.bind(action="monitor_trash").warning(
                "Could not start file monitor for trash: %s",
                exc,
            )

    def stop(self) -> None:
        """Cancel the trash file monitor."""
        if self._monitor:
            self._monitor.cancel()
            self._monitor = None
        super().stop()

    def _on_trash_changed(self, *_args: object) -> None:
        """File monitor callback: re-count items and update icon."""
        self._item_count = _count_trash_items()
        self.present()

    def _empty_trash(self) -> None:
        """Empty trash via DBus, with fallback to Gio deletion."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            for bus_name, obj_path in [
                ("org.mate.Caja", "/org/mate/Caja"),
                ("org.gnome.Nautilus", "/org/gnome/Nautilus"),
            ]:
                try:
                    bus.call_sync(
                        bus_name,
                        obj_path,
                        "org.gnome.Nautilus.FileOperations",
                        "EmptyTrash",
                        None,
                        None,
                        Gio.DBusCallFlags.NONE,
                        -1,
                        None,
                    )
                    return
                except GLib.Error as exc:
                    _log.bind(action="empty_trash_dbus").debug(
                        "DBus EmptyTrash failed for %s at %s: %s",
                        bus_name,
                        obj_path,
                        exc,
                    )
        except GLib.Error as exc:
            _log.bind(action="empty_trash_dbus").debug(
                "Could not connect to session bus for trash cleanup: %s",
                exc,
            )

        self._delete_trash_contents()

    def _delete_trash_contents(self) -> None:
        """Delete all top-level items in trash via Gio.File.delete()."""
        trash = Gio.File.new_for_uri("trash:///")
        try:
            enumerator = trash.enumerate_children(
                Gio.FILE_ATTRIBUTE_STANDARD_NAME, Gio.FileQueryInfoFlags.NONE, None
            )
        except GLib.Error as exc:
            _log.bind(action="empty_trash_delete").debug(
                "Could not enumerate trash for deletion: %s",
                exc,
            )
            return

        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            child = trash.get_child(info.get_name())
            try:
                child.delete(None)
            except GLib.Error as exc:
                _log.bind(action="empty_trash_delete").debug(
                    "Could not delete trash item %s: %s",
                    info.get_name(),
                    exc,
                )
        enumerator.close(None)
