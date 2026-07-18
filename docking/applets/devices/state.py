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

"""Presentation state for mounted devices."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from os.path import normpath
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

from docking.applets.devices.unix_mounts import NativeNetworkMount
from docking.applets.tooltip import structured_tooltip
from docking.i18n import _


class RootLike(Protocol):
    def get_path(self) -> str | None: ...
    def get_uri(self) -> str: ...


class MountLike(Protocol):
    def get_name(self) -> str | None: ...
    def get_root(self) -> RootLike: ...
    def get_icon(self) -> object: ...
    def get_uuid(self) -> str | None: ...


class VolumeMonitorLike(Protocol):
    def get_mounts(self) -> Sequence[MountLike]: ...


@dataclass(frozen=True, slots=True)
class MountedDevice:
    """One user-visible mount shown in the Devices stack."""

    key: str
    name: str
    uri: str
    mount_path: str
    icon: object | None
    is_network: bool = False


def mounted_devices(
    monitor: VolumeMonitorLike,
    native_mounts: Sequence[NativeNetworkMount] = (),
) -> list[MountedDevice]:
    """Merge desktop-visible mounts with native network filesystems."""
    devices: list[MountedDevice] = []
    device_indexes: dict[str, int] = {}
    for mount in monitor.get_mounts():
        try:
            root = mount.get_root()
            uri = str(root.get_uri() or "").strip()
            path = root.get_path()
        except Exception:
            continue
        if not uri:
            continue
        mount_path = str(path).strip() if path else _display_uri(uri)
        identity = _mount_identity(uri=uri, mount_path=mount_path)
        if identity in device_indexes:
            continue
        device_indexes[identity] = len(devices)
        devices.append(
            MountedDevice(
                key=_mount_key(mount=mount, uri=uri),
                name=_mount_name(mount=mount, uri=uri),
                uri=uri,
                mount_path=mount_path,
                icon=_mount_icon(mount),
                is_network=urlparse(uri).scheme not in {"", "file"},
            )
        )

    for native in native_mounts:
        mount_path = str(native.mount_path).strip()
        if not mount_path:
            continue
        try:
            uri = Path(mount_path).as_uri()
        except ValueError:
            continue
        identity = _mount_identity(uri=uri, mount_path=mount_path)
        existing_index = device_indexes.get(identity)
        if existing_index is not None:
            devices[existing_index] = replace(
                devices[existing_index],
                is_network=True,
            )
            continue
        device_indexes[identity] = len(devices)
        devices.append(
            MountedDevice(
                key=f"unix:{identity}",
                name=_native_mount_name(native),
                uri=uri,
                mount_path=mount_path,
                icon=native.icon,
                is_network=True,
            )
        )
    return sorted(devices, key=lambda device: (device.name.casefold(), device.uri))


def devices_tooltip(devices: Sequence[MountedDevice]) -> str:
    """Build the Devices applet tooltip."""
    if not devices:
        return structured_tooltip(
            title=_("Devices"),
            primary=_("No mounted devices"),
        )
    details = [device.mount_path for device in devices[:3] if device.mount_path]
    if len(devices) > 3:
        details.append(_("{count} more").format(count=len(devices) - 3))
    return structured_tooltip(
        title=_("Devices"),
        primary=_("{count} mounted device(s)").format(count=len(devices)),
        details=details,
    )


def _mount_key(*, mount: MountLike, uri: str) -> str:
    try:
        uuid = mount.get_uuid()
    except Exception:
        uuid = None
    return f"uuid:{uuid}:{uri}" if uuid else uri


def _mount_name(*, mount: MountLike, uri: str) -> str:
    try:
        name = mount.get_name()
    except Exception:
        name = None
    normalized = str(name or "").strip()
    if normalized:
        return normalized
    parsed = urlparse(uri)
    path_name = unquote(parsed.path).rstrip("/").rsplit("/", 1)[-1]
    return path_name or parsed.hostname or _("Mounted Device")


def _mount_icon(mount: MountLike) -> object | None:
    try:
        return mount.get_icon()
    except Exception:
        return None


def _native_mount_name(mount: NativeNetworkMount) -> str:
    name = str(mount.name).strip()
    if name:
        return name
    path_name = Path(normpath(mount.mount_path)).name
    return path_name or mount.source or _("Mounted Device")


def _mount_identity(*, uri: str, mount_path: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme in {"", "file"} and mount_path.startswith("/"):
        return f"path:{normpath(mount_path)}"
    return f"uri:{uri.rstrip('/')}"


def _display_uri(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return unquote(parsed.path) or uri
    return uri
