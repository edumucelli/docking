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

"""USB Watch applet behavior and GTK wiring."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.usbwatch import meta
from docking.applets.usbwatch.render import create_usbwatch_icon
from docking.applets.usbwatch.state import (
    MountedUsbDevice,
    mounted_usb_devices,
    usbwatch_tooltip,
)
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="usbwatch"), applet_id=meta.id)


class UsbWatchApplet(Applet):
    """Shows mounted removable USB devices and provides safe remove actions."""

    id = meta.id
    name = _("USB Watch")
    icon_name = "drive-removable-media-usb"
    supports_system_icon = True

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._monitor = Gio.VolumeMonitor.get()
        self._devices: list[MountedUsbDevice] = mounted_usb_devices(self._monitor)
        self._handler_ids: list[int] = []
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_docking_icon(self, size: int):
        return create_usbwatch_icon(size=size, device_count=len(self._devices))

    def system_icon_name(self) -> str:
        if self._devices:
            return "drive-removable-media-usb"
        return "drive-removable-media"

    def refresh_tooltip(self) -> None:
        self.item.name = usbwatch_tooltip(self._devices)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        if not self._devices:
            return [disabled_menu_item(_("No mounted USB devices"), gtk=Gtk)]

        status: list[Gtk.MenuItem] = []
        manage: list[Gtk.MenuItem] = []
        for device in self._devices:
            status.append(disabled_menu_item(device.name, gtk=Gtk))
            if device.mount_path:
                status.append(disabled_menu_item(device.mount_path, gtk=Gtk))
            remove_item = Gtk.MenuItem(
                label=_("Safely Remove {name}").format(name=device.name)
            )
            remove_item.set_sensitive(device.can_unmount or device.can_eject)
            remove_item.connect(
                "activate",
                lambda _widget, d=device: self._safe_remove(device=d),
            )
            manage.append(remove_item)
        return menu_sections(status=status, manage=manage, gtk=Gtk)

    def start(self, notify: Callable[[], None]) -> None:
        """Monitor mount/device changes."""
        super().start(notify=notify)
        for signal_name in (
            "mount-added",
            "mount-changed",
            "mount-removed",
            "volume-added",
            "volume-changed",
            "volume-removed",
            "drive-changed",
            "drive-connected",
            "drive-disconnected",
        ):
            self._handler_ids.append(
                self._monitor.connect(signal_name, self._on_changed)
            )

    def stop(self) -> None:
        """Disconnect volume monitor handlers."""
        for handler_id in self._handler_ids:
            self._monitor.disconnect(handler_id)
        self._handler_ids = []
        super().stop()

    def _on_changed(self, *_args: object) -> None:
        self._refresh_devices()

    def _refresh_devices(self) -> None:
        self._devices = mounted_usb_devices(self._monitor)
        self.present()

    def _safe_remove(self, *, device: MountedUsbDevice) -> None:
        operation = Gtk.MountOperation()
        if device.can_unmount:
            device.mount.unmount_with_operation(
                Gio.MountUnmountFlags.NONE,
                operation,
                None,
                self._on_unmount_finished,
                device,
            )
            return
        self._eject_device(device=device, operation=operation)

    def _on_unmount_finished(self, mount, result, device: MountedUsbDevice) -> None:
        try:
            mount.unmount_with_operation_finish(result)
        except GLib.Error as exc:
            log.bind(action="unmount", device=device.name).warning(
                "Failed to unmount %s: %s",
                device.name,
                exc,
            )
            self._refresh_devices()
            return
        if device.can_eject:
            self._eject_device(device=device, operation=Gtk.MountOperation())
            return
        self._refresh_devices()

    def _eject_device(
        self,
        *,
        device: MountedUsbDevice,
        operation: Gtk.MountOperation,
    ) -> None:
        if device.mount.can_eject():
            device.mount.eject_with_operation(
                Gio.MountUnmountFlags.NONE,
                operation,
                None,
                self._on_mount_eject_finished,
                device,
            )
            return
        if device.drive.can_eject():
            device.drive.eject_with_operation(
                Gio.MountUnmountFlags.NONE,
                operation,
                None,
                self._on_drive_eject_finished,
                device,
            )
            return
        self._refresh_devices()

    def _on_mount_eject_finished(self, mount, result, device: MountedUsbDevice) -> None:
        try:
            mount.eject_with_operation_finish(result)
        except GLib.Error as exc:
            log.bind(action="eject", device=device.name).warning(
                "Failed to eject %s: %s",
                device.name,
                exc,
            )
        self._refresh_devices()

    def _on_drive_eject_finished(self, drive, result, device: MountedUsbDevice) -> None:
        try:
            drive.eject_with_operation_finish(result)
        except GLib.Error as exc:
            log.bind(action="eject", device=device.name).warning(
                "Failed to eject %s: %s",
                device.name,
                exc,
            )
        self._refresh_devices()
