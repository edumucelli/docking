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

"""State helpers for mounted removable USB/storage devices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from docking.applets.tooltip import structured_tooltip
from docking.i18n import _


class RootLike(Protocol):
    def get_path(self) -> str | None: ...
    def get_uri(self) -> str: ...


class DriveLike(Protocol):
    def get_name(self) -> str | None: ...
    def is_removable(self) -> bool: ...
    def can_eject(self) -> bool: ...


class VolumeLike(Protocol):
    def get_drive(self) -> DriveLike | None: ...


class MountLike(Protocol):
    def get_name(self) -> str | None: ...
    def get_root(self) -> RootLike: ...
    def get_volume(self) -> VolumeLike | None: ...
    def can_unmount(self) -> bool: ...
    def can_eject(self) -> bool: ...


class VolumeMonitorLike(Protocol):
    def get_mounts(self) -> list[MountLike]: ...


@dataclass(frozen=True, slots=True)
class MountedUsbDevice:
    """Mounted removable device shown by the USB Watch applet."""

    name: str
    mount_path: str
    can_unmount: bool
    can_eject: bool
    mount: MountLike
    drive: DriveLike


def mounted_usb_devices(monitor: VolumeMonitorLike) -> list[MountedUsbDevice]:
    """Return mounted removable drives visible through a Gio.VolumeMonitor."""
    devices: list[MountedUsbDevice] = []
    for mount in monitor.get_mounts():
        volume = mount.get_volume()
        if volume is None:
            continue
        drive = volume.get_drive()
        if drive is None:
            continue
        if not drive.is_removable() and not drive.can_eject():
            continue
        devices.append(
            MountedUsbDevice(
                name=_device_name(mount=mount, drive=drive),
                mount_path=_mount_path(mount=mount),
                can_unmount=mount.can_unmount(),
                can_eject=mount.can_eject() or drive.can_eject(),
                mount=mount,
                drive=drive,
            )
        )
    return sorted(devices, key=lambda device: device.name.casefold())


def usbwatch_tooltip(devices: list[MountedUsbDevice]) -> str:
    """Build the USB Watch tooltip."""
    if not devices:
        return structured_tooltip(
            title=_("USB Watch"),
            primary=_("No mounted USB devices"),
        )
    details = [device.mount_path for device in devices[:3] if device.mount_path]
    if len(devices) > 3:
        details.append(_("{count} more").format(count=len(devices) - 3))
    return structured_tooltip(
        title=_("USB Watch"),
        primary=_("{count} mounted device(s)").format(count=len(devices)),
        details=details,
    )


def _device_name(*, mount: MountLike, drive: DriveLike) -> str:
    name = mount.get_name() or drive.get_name()
    return str(name).strip() or _("Removable Drive")


def _mount_path(*, mount: MountLike) -> str:
    root = mount.get_root()
    path = root.get_path()
    if path:
        return path
    return root.get_uri()
