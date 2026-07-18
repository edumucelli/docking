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

"""Compatibility helpers for native network mounts.

GLib exposes the Unix mount table through two introspection layouts. Older
releases publish the functions in ``Gio`` with names such as
``unix_mounts_get``. GLib 2.84 moved the API to the ``GioUnix`` namespace and
introduced ``mount_entries_get``. Keeping that version handling here lets the
Devices applet consume one small, source-neutral record on every supported
distribution.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio

try:
    gi.require_version("GioUnix", "2.0")
    _gio_unix: Any | None = import_module("gi.repository.GioUnix")
except (ImportError, ValueError):
    _gio_unix = None


_NETWORK_FILESYSTEMS = frozenset(
    {
        "afs",
        "ceph",
        "cifs",
        "coda",
        "davfs",
        "davfs2",
        "fuse.ceph",
        "fuse.curlftpfs",
        "fuse.davfs",
        "fuse.davfs2",
        "fuse.glusterfs",
        "fuse.rclone",
        "fuse.smbnetfs",
        "fuse.sshfs",
        "glusterfs",
        "nfs",
        "nfs4",
        "smb3",
        "sshfs",
    }
)


@dataclass(frozen=True, slots=True)
class NativeNetworkMount:
    """One active network filesystem from the Unix mount table."""

    mount_path: str
    source: str
    fs_type: str
    name: str
    icon: object | None


def read_network_mounts(*, api: Any | None = None) -> list[NativeNetworkMount]:
    """Return supported network filesystems from the active mount table."""
    namespace = api if api is not None else _default_api()
    try:
        entries = _mount_entries(namespace)
    except Exception:
        return []

    mounts: list[NativeNetworkMount] = []
    for entry in entries:
        try:
            fs_type = str(_entry_value(namespace, entry, "get_fs_type") or "")
            fs_type = fs_type.strip().casefold()
            if fs_type not in _NETWORK_FILESYSTEMS:
                continue
            mount_path = str(
                _entry_value(namespace, entry, "get_mount_path") or ""
            ).strip()
            if not mount_path.startswith("/"):
                continue
            source = str(
                _entry_value(namespace, entry, "get_device_path") or ""
            ).strip()
            name = str(
                _entry_value(namespace, entry, "guess_name", required=False) or ""
            ).strip()
            icon = _entry_value(namespace, entry, "guess_icon", required=False)
        except Exception:
            continue
        mounts.append(
            NativeNetworkMount(
                mount_path=mount_path,
                source=source,
                fs_type=fs_type,
                name=name,
                icon=icon,
            )
        )
    return mounts


def get_mount_monitor(*, api: Any | None = None) -> Any | None:
    """Return the Unix mount monitor, or ``None`` when it is unavailable."""
    namespace = api if api is not None else _default_api()
    monitor_type = getattr(namespace, "MountMonitor", None)
    if monitor_type is None:
        monitor_type = getattr(namespace, "UnixMountMonitor", None)
    if monitor_type is None:
        return None
    try:
        return monitor_type.get()
    except Exception:
        return None


def _default_api() -> Any:
    return _gio_unix if _gio_unix is not None else Gio


def _mount_entries(api: Any) -> list[Any]:
    getter = None
    for name in ("mount_entries_get", "mounts_get", "unix_mounts_get"):
        getter = getattr(api, name, None)
        if getter is not None:
            break
    if getter is None:
        return []
    result = getter()
    if isinstance(result, tuple):
        result = result[0]
    return list(result or [])


def _entry_value(
    api: Any,
    entry: Any,
    suffix: str,
    *,
    required: bool = True,
) -> Any:
    for name in (
        f"mount_entry_{suffix}",
        f"mount_{suffix}",
        f"unix_mount_{suffix}",
    ):
        accessor = getattr(api, name, None)
        if accessor is not None:
            return accessor(entry)
    accessor = getattr(entry, suffix, None)
    if accessor is not None:
        return accessor()
    if required:
        raise AttributeError(f"Unix mount accessor {suffix!r} is unavailable")
    return None
