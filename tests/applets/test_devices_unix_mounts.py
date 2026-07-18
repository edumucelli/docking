"""Tests for native network mount compatibility helpers."""

from __future__ import annotations

from types import SimpleNamespace

from docking.applets.devices.unix_mounts import (
    get_mount_monitor,
    read_network_mounts,
)


class _Entry:
    def __init__(
        self,
        *,
        path: str,
        source: str,
        fs_type: str,
        name: str = "",
        icon: object | None = None,
    ) -> None:
        self.path = path
        self.source = source
        self.fs_type = fs_type
        self.name = name
        self.icon = icon


class _MonitorType:
    monitor = object()

    @classmethod
    def get(cls):
        return cls.monitor


def _old_api(entries: list[_Entry]):
    return SimpleNamespace(
        unix_mounts_get=lambda: (entries, 123),
        unix_mount_get_mount_path=lambda entry: entry.path,
        unix_mount_get_device_path=lambda entry: entry.source,
        unix_mount_get_fs_type=lambda entry: entry.fs_type,
        unix_mount_guess_name=lambda entry: entry.name,
        unix_mount_guess_icon=lambda entry: entry.icon,
        UnixMountMonitor=_MonitorType,
    )


def _new_api(entries: list[_Entry]):
    return SimpleNamespace(
        mount_entries_get=lambda: (entries, 456),
        mount_entry_get_mount_path=lambda entry: entry.path,
        mount_entry_get_device_path=lambda entry: entry.source,
        mount_entry_get_fs_type=lambda entry: entry.fs_type,
        mount_entry_guess_name=lambda entry: entry.name,
        mount_entry_guess_icon=lambda entry: entry.icon,
        MountMonitor=_MonitorType,
    )


def test_legacy_gio_api_returns_only_supported_network_filesystems():
    icon = object()
    api = _old_api(
        [
            _Entry(
                path="/mnt/team",
                source="//fileserver/team",
                fs_type="cifs",
                name="Team Share",
                icon=icon,
            ),
            _Entry(path="/", source="/dev/sda1", fs_type="ext4"),
            _Entry(path="/run/user/1000/doc", source="portal", fs_type="fuse.portal"),
        ]
    )

    mounts = read_network_mounts(api=api)

    assert len(mounts) == 1
    assert mounts[0].mount_path == "/mnt/team"
    assert mounts[0].source == "//fileserver/team"
    assert mounts[0].fs_type == "cifs"
    assert mounts[0].name == "Team Share"
    assert mounts[0].icon is icon
    assert get_mount_monitor(api=api) is _MonitorType.monitor


def test_glib_284_api_supports_nfs_sshfs_webdav_and_rclone():
    api = _new_api(
        [
            _Entry(path="/mnt/nfs", source="server:/data", fs_type="nfs4"),
            _Entry(path="/mnt/ssh", source="user@server:/", fs_type="fuse.sshfs"),
            _Entry(path="/mnt/dav", source="https://example.test", fs_type="davfs2"),
            _Entry(path="/mnt/cloud", source="cloud:", fs_type="fuse.rclone"),
        ]
    )

    mounts = read_network_mounts(api=api)

    assert [mount.fs_type for mount in mounts] == [
        "nfs4",
        "fuse.sshfs",
        "davfs2",
        "fuse.rclone",
    ]
    assert get_mount_monitor(api=api) is _MonitorType.monitor


def test_invalid_entries_and_unavailable_apis_are_ignored():
    invalid_path = _Entry(path="relative/share", source="server:/", fs_type="nfs")
    broken_api = SimpleNamespace(
        mount_entries_get=lambda: [invalid_path],
        mount_entry_get_mount_path=lambda entry: entry.path,
        mount_entry_get_device_path=lambda entry: entry.source,
        mount_entry_get_fs_type=lambda entry: entry.fs_type,
    )

    assert read_network_mounts(api=broken_api) == []
    assert read_network_mounts(api=SimpleNamespace()) == []
    assert get_mount_monitor(api=SimpleNamespace()) is None
