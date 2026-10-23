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

"""Mounted Devices applet backed by the reusable item stack."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gio, Gtk

from docking.applets.base import TargetServicesApplet
from docking.applets.devices import meta
from docking.applets.devices.render import create_devices_icon
from docking.applets.devices.state import (
    MountedDevice,
    devices_tooltip,
    mounted_devices,
)
from docking.applets.devices.unix_mounts import (
    get_mount_monitor,
    read_network_mounts,
)
from docking.core.icons import IconSource
from docking.i18n import _
from docking.platform.icons import IconLoader
from docking.platform.targets import TargetService
from docking.ui.stack import StackContent, StackEntry

if TYPE_CHECKING:
    from docking.core.config import Config

_MONITOR_SIGNALS = (
    "mount-added",
    "mount-changed",
    "mount-removed",
    "volume-added",
    "volume-changed",
    "volume-removed",
    "drive-changed",
    "drive-connected",
    "drive-disconnected",
)


class DevicesApplet(TargetServicesApplet):
    """Show desktop-visible devices and native network mounts in a live stack."""

    id = meta.id
    name = _("Devices")
    icon_name = "drive-harddisk"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(
        self,
        icon_size: int,
        config: Config,
        *,
        icon_loader: IconLoader,
        target_service: TargetService,
    ) -> None:
        self._monitor = Gio.VolumeMonitor.get()
        self._unix_mount_monitor = get_mount_monitor()
        self._devices: list[MountedDevice] = mounted_devices(
            self._monitor,
            read_network_mounts(),
        )
        self._handler_ids: list[tuple[object, int]] = []
        super().__init__(
            icon_size=icon_size,
            config=config,
            icon_loader=icon_loader,
            target_service=target_service,
        )
        self.present()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_devices_icon(size=size, device_count=len(self._devices))

    def system_icon_name(self) -> str:
        return "drive-harddisk" if self._devices else "drive-harddisk-symbolic"

    def refresh_tooltip(self) -> None:
        self.item.name = devices_tooltip(self._devices)

    def stack_content(self, icon_size: int) -> StackContent | None:
        if not self._devices:
            return None
        return StackContent(
            entries=tuple(
                StackEntry(
                    key=device.key,
                    label=device.name,
                    icon=self._stack_icon(device=device, size=icon_size),
                    activate=lambda uri=device.uri: self._open_device(uri=uri),
                )
                for device in self._devices
            ),
        )

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        refresh = Gtk.MenuItem(label=_("Refresh Devices"))
        refresh.connect("activate", lambda _widget: self._refresh_devices())
        return [refresh]

    def start(self, notify: Callable[[], None]) -> None:
        """Subscribe to desktop volume and mount changes."""
        super().start(notify=notify)
        if self._handler_ids:
            return
        for signal_name in _MONITOR_SIGNALS:
            self._handler_ids.append(
                (
                    self._monitor,
                    self._monitor.connect(signal_name, self._on_changed),
                )
            )
        if self._unix_mount_monitor is not None:
            self._handler_ids.append(
                (
                    self._unix_mount_monitor,
                    self._unix_mount_monitor.connect(
                        "mounts-changed",
                        self._on_changed,
                    ),
                )
            )

    def stop(self) -> None:
        """Disconnect volume and Unix mount-monitor signal handlers."""
        for monitor, handler_id in self._handler_ids:
            monitor.disconnect(handler_id)
        self._handler_ids = []
        super().stop()

    def _on_changed(self, *_args: object) -> None:
        self._refresh_devices()

    def _refresh_devices(self) -> None:
        devices = mounted_devices(self._monitor, read_network_mounts())
        changed = _device_signature(devices) != _device_signature(self._devices)
        self._devices = devices
        if not changed:
            return
        self.present()

    def _stack_icon(
        self,
        *,
        device: MountedDevice,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        return _load_mount_icon(
            device=device,
            size=size,
            icon_loader=self._icon_loader,
        )

    def _open_device(self, *, uri: str) -> None:
        self._target_service.open_target(uri)


def _load_mount_icon(
    *,
    device: MountedDevice,
    size: int,
    icon_loader: IconLoader,
) -> GdkPixbuf.Pixbuf | None:
    if device.icon is not None:
        icon = icon_loader.load_gicon(device.icon, size)
        if icon is not None:
            return icon
    fallback = "folder-remote" if device.is_network else "drive-harddisk"
    return icon_loader.load_icon(fallback, size)


def _device_signature(devices: list[MountedDevice]) -> tuple[tuple[str, ...], ...]:
    return tuple(
        (
            device.key,
            device.name,
            device.uri,
            device.mount_path,
            str(device.is_network),
        )
        for device in devices
    )
