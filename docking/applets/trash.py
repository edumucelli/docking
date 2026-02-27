"""Trash applet -- shows trash state, opens and empties trash.

Icon switches between user-trash (empty) and user-trash-full (has items).
Monitors trash:/// via Gio.FileMonitor for real-time updates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="trash")


def _count_trash_items() -> int:
    """Count top-level items in trash:/// via Gio enumerator."""
    trash = Gio.File.new_for_uri("trash:///")
    try:
        enumerator = trash.enumerate_children(
            Gio.FILE_ATTRIBUTE_STANDARD_NAME, Gio.FileQueryInfoFlags.NONE, None
        )
    except GLib.Error:
        return 0
    count = 0
    while enumerator.next_file(None) is not None:
        count += 1
    enumerator.close(None)
    return count


class TrashApplet(Applet):
    """Shows trash state icon; click opens, menu allows emptying.

    Icon: user-trash (empty) or user-trash-full (has items).
    Tooltip: item count (e.g. "3 items in Trash").
    """

    id = "trash"
    name = "Trash"
    icon_name = "user-trash"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._item_count = _count_trash_items()
        self._monitor: Gio.FileMonitor | None = None
        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render trash icon; switches between empty/full based on item count."""
        icon_name = "user-trash-full" if self._item_count > 0 else "user-trash"
        # Update tooltip (guard: item not yet set on first call from super().__init__)
        if hasattr(self, "item"):
            if self._item_count == 0:
                self.item.name = "No items in Trash"
            elif self._item_count == 1:
                self.item.name = "1 item in Trash"
            else:
                self.item.name = f"{self._item_count} items in Trash"
        return load_theme_icon(name=icon_name, size=size)

    def on_clicked(self) -> None:
        """Open trash folder in the default file manager."""
        try:
            Gio.AppInfo.launch_default_for_uri("trash:///", None)
        except GLib.Error as e:
            _log.warning("Failed to open trash: %s", e)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Return 'Open Trash' and 'Empty Trash' menu items."""
        items: list[Gtk.MenuItem] = []

        open_item = Gtk.MenuItem(label="Open Trash")
        open_item.connect("activate", lambda _: self.on_clicked())
        items.append(open_item)

        empty_item = Gtk.MenuItem(label="Empty Trash")
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
        except GLib.Error:
            _log.warning("Could not start file monitor for trash")

    def stop(self) -> None:
        """Cancel the trash file monitor."""
        if self._monitor:
            self._monitor.cancel()
            self._monitor = None
        super().stop()

    def _on_trash_changed(self, *_args: object) -> None:
        """File monitor callback: re-count items and update icon."""
        self._item_count = _count_trash_items()
        self.refresh_icon()

    def _empty_trash(self) -> None:
        """Empty trash via DBus (Caja/Nautilus) with fallback to Gio deletion.

        Tries the file manager's DBus EmptyTrash method first (shows native
        confirmation dialog). Falls back to direct Gio deletion if DBus fails.
        """
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            # Try Caja (MATE) first, then Nautilus (GNOME) -- both implement
            # the org.gnome.Nautilus.FileOperations interface
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
                except GLib.Error:
                    continue
        except GLib.Error:
            pass

        # Fallback: delete children directly via Gio
        self._delete_trash_contents()

    def _delete_trash_contents(self) -> None:
        """Delete all top-level items in trash via Gio.File.delete()."""
        trash = Gio.File.new_for_uri("trash:///")
        try:
            enumerator = trash.enumerate_children(
                Gio.FILE_ATTRIBUTE_STANDARD_NAME, Gio.FileQueryInfoFlags.NONE, None
            )
        except GLib.Error:
            return
        while True:
            info = enumerator.next_file(None)
            if info is None:
                break
            child = trash.get_child(info.get_name())
            try:
                child.delete(None)
            except GLib.Error:
                pass
        enumerator.close(None)
