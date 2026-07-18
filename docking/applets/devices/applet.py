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
from typing import TYPE_CHECKING, cast
from urllib.parse import urlparse

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk

import docking.platform.launcher as launcher_mod
from docking.applets.base import Applet, load_theme_icon
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
from docking.log import get_logger, with_context
from docking.ui.stack import StackContent, StackEntry

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="devices"), applet_id=meta.id)

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


class DevicesApplet(Applet):
    """Show desktop-visible devices and native network mounts in a live stack."""

    id = meta.id
    name = _("Devices")
    icon_name = "drive-harddisk"
    icon_source_options = (IconSource.DOCKING, IconSource.SYSTEM)

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._monitor = Gio.VolumeMonitor.get()
        self._unix_mount_monitor = get_mount_monitor()
        self._devices: list[MountedDevice] = mounted_devices(
            self._monitor,
            read_network_mounts(),
        )
        self._handler_ids: list[tuple[object, int]] = []
        self._stack_icons: dict[tuple[str, int], GdkPixbuf.Pixbuf | None] = {}
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_devices_icon(size=size, device_count=len(self._devices))

    def system_icon_name(self) -> str:
        return "drive-harddisk" if self._devices else "drive-harddisk-symbolic"

    def refresh_tooltip(self) -> None:
        self.item.name = devices_tooltip(self._devices)

    def stack_content(self, icon_size: int) -> StackContent:
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
            empty_label=_("No mounted devices"),
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
        self._stack_icons.clear()
        self.present()

    def _stack_icon(
        self,
        *,
        device: MountedDevice,
        size: int,
    ) -> GdkPixbuf.Pixbuf | None:
        cache_key = (device.key, size)
        if cache_key in self._stack_icons:
            return self._stack_icons[cache_key]
        icon = _load_mount_icon(device=device, size=size)
        self._stack_icons[cache_key] = icon
        return icon

    def _open_device(self, *, uri: str) -> None:
        if urlparse(uri).scheme == "file" and launcher_mod.open_target(uri):
            return
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            log.bind(action="open_device", uri=uri).warning(
                "Failed to open mounted device %s: %s",
                uri,
                exc,
            )


def _load_mount_icon(
    *,
    device: MountedDevice,
    size: int,
) -> GdkPixbuf.Pixbuf | None:
    if device.icon is not None:
        try:
            theme = Gtk.IconTheme.get_default()
            if theme is not None:
                icon_info = theme.lookup_by_gicon(
                    cast(Gio.Icon, device.icon),
                    size,
                    Gtk.IconLookupFlags.FORCE_SIZE,
                )
                if icon_info is not None:
                    return icon_info.load_icon()
        except (AttributeError, GLib.Error):
            pass
    fallback = "folder-remote" if device.is_network else "drive-harddisk"
    return load_theme_icon(name=fallback, size=size)


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
