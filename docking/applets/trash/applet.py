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
from docking.applets.menu import menu_sections
from docking.applets.trash import meta
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform.environment import detect_desktop

from .backend import TrashBackend, select_trash_backend
from .render import create_trash_icon, trash_tooltip

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="trash"), applet_id=meta.id)


class TrashApplet(Applet):
    """Shows trash state icon; click opens, menu allows emptying."""

    id = meta.id
    name = _("Trash")
    icon_name = "user-trash"
    supports_system_icon = True

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._desktop = detect_desktop()
        self._backend: TrashBackend = select_trash_backend(desktop=self._desktop)
        self._item_count = self._backend.count_items()
        self._monitor: Gio.FileMonitor | None = None
        super().__init__(icon_size, config)
        self.present()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_trash_icon(size=size, item_count=self._item_count)

    def system_icon_name(self) -> str:
        if self._item_count > 0:
            return "user-trash-full"
        return "user-trash"

    def refresh_tooltip(self) -> None:
        self.item.name = trash_tooltip(item_count=self._item_count)

    def on_clicked(self) -> None:
        """Open trash folder in the default file manager."""
        self._backend.open()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Return 'Open Trash' and 'Empty Trash' menu items."""
        open_item = Gtk.MenuItem(label=_("Open Trash"))
        open_item.connect("activate", lambda _: self.on_clicked())

        empty_item = Gtk.MenuItem(label=_("Empty Trash"))
        empty_item.set_sensitive(self._item_count > 0)
        empty_item.connect("activate", lambda _: self._empty_trash())

        return menu_sections(primary=[open_item], destructive=[empty_item], gtk=Gtk)

    def start(self, notify: Callable[[], None]) -> None:
        """Start trash monitoring for real-time icon updates."""
        super().start(notify)
        try:
            self._monitor = self._backend.monitor_file().monitor(
                Gio.FileMonitorFlags.NONE, None
            )
            self._monitor.connect("changed", self._on_trash_changed)
        except GLib.Error as exc:
            log.bind(action="monitor_trash").warning(
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
        self._item_count = self._backend.count_items()
        self.present()

    def _empty_trash(self) -> None:
        """Empty trash through the selected backend."""
        self._backend.empty(self._confirm_empty_trash)

    def _confirm_empty_trash(self) -> bool:
        dialog = Gtk.MessageDialog(
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=_("Empty all items from Trash?"),
        )
        dialog.format_secondary_text(
            _("All items in the Trash will be permanently deleted."),
        )
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.add_button(_("Empty Trash"), Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        dialog.set_position(Gtk.WindowPosition.MOUSE)
        response = dialog.run()
        dialog.destroy()
        return response == Gtk.ResponseType.OK
