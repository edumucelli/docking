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
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import unquote, urlparse

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
    mount: MountLike


def mounted_devices(monitor: VolumeMonitorLike) -> list[MountedDevice]:
    """Return every mount exposed by the desktop volume monitor."""
    devices: list[MountedDevice] = []
    seen_uris: set[str] = set()
    for mount in monitor.get_mounts():
        try:
            root = mount.get_root()
            uri = str(root.get_uri() or "").strip()
        except Exception:
            continue
        if not uri or uri in seen_uris:
            continue
        seen_uris.add(uri)
        path = root.get_path()
        mount_path = str(path).strip() if path else _display_uri(uri)
        devices.append(
            MountedDevice(
                key=_mount_key(mount=mount, uri=uri),
                name=_mount_name(mount=mount, uri=uri),
                uri=uri,
                mount_path=mount_path,
                icon=_mount_icon(mount),
                mount=mount,
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


def _display_uri(uri: str) -> str:
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        return unquote(parsed.path) or uri
    return uri
