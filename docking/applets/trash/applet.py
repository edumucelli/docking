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
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.trash import meta
from docking.core.icons import IconSource
from docking.i18n import _
from docking.log import get_logger, with_context
from docking.platform.environment import detect_desktop

from .backend import TrashBackend, TrashEmptyResult, select_trash_backend
from .render import add_trash_warning_badge, create_trash_icon, trash_tooltip

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="trash"), applet_id=meta.id)


class TrashApplet(Applet):
    """Shows trash state icon; click opens, menu allows emptying."""

    id = meta.id
    name = _("Trash")
    icon_name = "user-trash"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._desktop = detect_desktop()
        self._backend: TrashBackend = select_trash_backend(desktop=self._desktop)
        self._item_count = self._backend.count_items()
        self._monitors: list[Gio.FileMonitor] = []
        self._volume_monitor: Gio.VolumeMonitor | None = None
        self._volume_monitor_handlers: list[int] = []
        self._action_error = ""
        self._empty_attention = False
        super().__init__(icon_size, config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        icon = super().create_icon(size=size)
        if icon is not None and self._empty_attention:
            return add_trash_warning_badge(icon)
        return icon

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_trash_icon(size=size, item_count=self._item_count)

    def system_icon_name(self) -> str:
        if self._item_count > 0:
            return "user-trash-full"
        return "user-trash"

    def refresh_tooltip(self) -> None:
        text = trash_tooltip(item_count=self._item_count)
        if self._action_error:
            text = f"{text}\n{self._action_error}"
        self.item.name = text

    def on_clicked(self) -> None:
        """Open trash folder in the default file manager."""
        self._backend.open()

    def accepts_drop_uris(self) -> bool:
        """Allow local files/folders to be dropped onto the Trash icon."""
        return True

    def on_drop_uris(self, uris: list[str]) -> bool:
        """Move dropped local files/folders to Trash."""
        moved = False
        failed = False
        for uri in uris:
            file = self._file_from_drop_uri(uri)
            if file is None:
                failed = True
                continue
            try:
                if file.trash(None):
                    moved = True
                else:
                    failed = True
            except GLib.Error as exc:
                failed = True
                log.bind(action="drop_to_trash").warning(
                    "Failed to move dropped item to trash: %s",
                    exc,
                )

        if moved:
            self._item_count = self._backend.count_items()
            self.present()
        elif failed:
            log.bind(action="drop_to_trash").debug(
                "Drop did not contain a local trashable file"
            )
        return moved

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Return 'Open Trash' and 'Empty Trash' menu items."""
        status: list[Gtk.MenuItem] = []
        if self._action_error:
            status.append(disabled_menu_item(self._action_error, gtk=Gtk))

        open_item = Gtk.MenuItem(label=_("Open Trash"))
        open_item.connect("activate", lambda _: self.on_clicked())

        empty_item = Gtk.MenuItem(label=_("Empty Trash"))
        empty_item.set_sensitive(self._item_count > 0)
        empty_item.connect("activate", lambda _: self._empty_trash())

        return menu_sections(
            status=status,
            primary=[open_item],
            destructive=[empty_item],
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        """Start trash monitoring for real-time icon updates."""
        super().start(notify)
        self._start_trash_monitors()
        self._volume_monitor = Gio.VolumeMonitor.get()
        self._volume_monitor_handlers = [
            self._volume_monitor.connect("mount-added", self._on_mounts_changed),
            self._volume_monitor.connect("mount-removed", self._on_mounts_changed),
        ]

    def stop(self) -> None:
        """Cancel the trash file monitor."""
        self._cancel_trash_monitors()
        if self._volume_monitor is not None:
            for handler_id in self._volume_monitor_handlers:
                self._volume_monitor.disconnect(handler_id)
            self._volume_monitor = None
            self._volume_monitor_handlers = []
        super().stop()

    def _start_trash_monitors(self) -> None:
        for file in self._backend.monitor_files():
            try:
                monitor = file.monitor(Gio.FileMonitorFlags.NONE, None)
            except GLib.Error as exc:
                log.bind(action="monitor_trash").warning(
                    "Could not start file monitor for trash: %s",
                    exc,
                )
                continue
            monitor.connect("changed", self._on_trash_changed)
            self._monitors.append(monitor)

    def _cancel_trash_monitors(self) -> None:
        for monitor in self._monitors:
            monitor.cancel()
        self._monitors = []

    def _on_trash_changed(self, *_args: object) -> None:
        """File monitor callback: re-count items and update icon."""
        self._item_count = self._backend.count_items()
        if self._item_count == 0:
            self._clear_empty_attention()
        self.present()

    def _on_mounts_changed(self, *_args: object) -> None:
        """Mount changes can add or remove per-volume trash directories."""
        self._cancel_trash_monitors()
        self._start_trash_monitors()
        self._on_trash_changed()

    def _empty_trash(self) -> None:
        """Empty trash through the selected backend."""
        result = self._backend.empty(self._confirm_empty_trash)
        self._handle_empty_result(result)

    def _handle_empty_result(self, result: TrashEmptyResult | None) -> None:
        if result is None:
            self._item_count = self._backend.count_items()
            if self._item_count == 0:
                self._clear_empty_attention()
            self.present()
            return

        self._item_count = result.remaining_count
        if self._item_count == 0:
            self._clear_empty_attention()
            self.present()
            return

        if not result.attempted:
            if result.delegated:
                self.present()
            return

        self._action_error = self._empty_error_text(
            permission_denied=result.permission_denied,
        )
        self._empty_attention = True
        self.present()

    def _clear_empty_attention(self) -> None:
        self._action_error = ""
        self._empty_attention = False

    @staticmethod
    def _empty_error_text(*, permission_denied: bool) -> str:
        if permission_denied:
            return _(
                "Could not empty Trash. "
                "Some items may require administrator permissions."
            )
        return _("Could not empty Trash.")

    @staticmethod
    def _file_from_drop_uri(uri: str) -> Gio.File | None:
        text = uri.strip()
        if not text:
            return None
        if text.startswith("file://"):
            file = Gio.File.new_for_uri(text)
        elif text.startswith("/"):
            file = Gio.File.new_for_path(text)
        else:
            return None
        if file.get_path() is None:
            return None
        return file

    def _confirm_empty_trash(self) -> bool:
        dialog = Gtk.MessageDialog(
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=_("Empty all items from Trash?"),
        )
        self.register_popup_surface(dialog)
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
