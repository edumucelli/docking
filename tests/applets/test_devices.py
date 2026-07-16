"""Tests for the mounted Devices stack applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.devices.applet as devices_applet_mod
from docking.applets.base import load_catalog_icon
from docking.applets.devices.applet import DevicesApplet
from docking.applets.devices.render import create_devices_icon
from docking.applets.devices.state import devices_tooltip, mounted_devices


class _FakeRoot:
    def __init__(self, *, path: str | None, uri: str) -> None:
        self._path = path
        self._uri = uri

    def get_path(self) -> str | None:
        return self._path

    def get_uri(self) -> str:
        return self._uri


class _FakeMount:
    def __init__(
        self,
        *,
        name: str | None,
        path: str | None,
        uri: str,
        uuid: str | None = None,
        icon: object | None = None,
    ) -> None:
        self._name = name
        self._root = _FakeRoot(path=path, uri=uri)
        self._uuid = uuid
        self._icon = icon

    def get_name(self) -> str | None:
        return self._name

    def get_root(self) -> _FakeRoot:
        return self._root

    def get_uuid(self) -> str | None:
        return self._uuid

    def get_icon(self) -> object | None:
        return self._icon


class _BrokenMount:
    def get_root(self):
        raise RuntimeError("gone")


class _FakeMonitor:
    def __init__(self, mounts: list[object] | None = None) -> None:
        self.mounts = list(mounts or [])
        self.callbacks: dict[str, object] = {}
        self.disconnected: list[int] = []
        self._handler_signals: dict[int, str] = {}
        self._next_handler_id = 1

    def get_mounts(self) -> list[object]:
        return list(self.mounts)

    def connect(self, signal_name: str, callback) -> int:
        handler_id = self._next_handler_id
        self._next_handler_id += 1
        self.callbacks[signal_name] = callback
        self._handler_signals[handler_id] = signal_name
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self.disconnected.append(handler_id)

    def emit(self, signal_name: str) -> None:
        self.callbacks[signal_name](self, object())


def _mount(
    name: str | None = "Data",
    *,
    path: str | None = "/mnt/data",
    uri: str = "file:///mnt/data",
    uuid: str | None = None,
    icon: object | None = None,
) -> _FakeMount:
    return _FakeMount(name=name, path=path, uri=uri, uuid=uuid, icon=icon)


def _applet(monkeypatch, monitor: _FakeMonitor) -> DevicesApplet:
    monkeypatch.setattr(
        devices_applet_mod.Gio.VolumeMonitor,
        "get",
        lambda: monitor,
    )
    return DevicesApplet(48)


def test_mounted_devices_includes_local_network_and_unbacked_mounts():
    internal = _mount("System", path="/", uri="file:///")
    removable = _mount(
        "USB Stick",
        path="/media/USB",
        uri="file:///media/USB",
        uuid="usb-1",
    )
    network = _mount(
        "Team Share",
        path=None,
        uri="smb://fileserver/team",
    )

    devices = mounted_devices(
        _FakeMonitor([removable, network, _BrokenMount(), internal])
    )

    assert [device.name for device in devices] == [
        "System",
        "Team Share",
        "USB Stick",
    ]
    assert [device.uri for device in devices] == [
        "file:///",
        "smb://fileserver/team",
        "file:///media/USB",
    ]
    assert devices[1].mount_path == "smb://fileserver/team"
    assert devices[2].key == "uuid:usb-1:file:///media/USB"


def test_mounted_devices_deduplicates_root_uri_and_uses_name_fallback():
    first = _mount(None, path="/media/Backup", uri="file:///media/Backup")
    duplicate = _mount("Duplicate", path="/media/Backup", uri="file:///media/Backup")

    devices = mounted_devices(_FakeMonitor([first, duplicate]))

    assert len(devices) == 1
    assert devices[0].name == "Backup"


def test_devices_tooltip_empty_and_populated():
    assert "No mounted devices" in devices_tooltip([])
    devices = mounted_devices(_FakeMonitor([_mount()]))

    tooltip = devices_tooltip(devices)

    assert "1 mounted device" in tooltip
    assert "/mnt/data" in tooltip


def test_devices_icon_renders_empty_and_populated():
    assert create_devices_icon(size=48, device_count=0) is not None
    assert create_devices_icon(size=48, device_count=3) is not None


def test_devices_catalog_icon_is_packaged():
    icon = load_catalog_icon(applet_id="devices", size=48)

    assert icon is not None
    assert icon.get_width() == 48


class TestDevicesApplet:
    def test_stack_lists_every_mount_and_opens_selected_device(self, monkeypatch):
        monitor = _FakeMonitor(
            [
                _mount("Data", path="/mnt/data", uri="file:///mnt/data"),
                _mount(
                    "Team Share",
                    path=None,
                    uri="smb://fileserver/team",
                ),
            ]
        )
        applet = _applet(monkeypatch, monitor)
        icon = object()
        monkeypatch.setattr(
            devices_applet_mod,
            "_load_mount_icon",
            lambda **_kwargs: icon,
        )
        open_target = MagicMock(return_value=True)
        monkeypatch.setattr(
            devices_applet_mod.launcher_mod,
            "open_target",
            open_target,
        )

        content = applet.stack_content(32)

        assert [entry.label for entry in content.entries] == ["Data", "Team Share"]
        assert [entry.icon for entry in content.entries] == [icon, icon]
        content.entries[0].activate()
        open_target.assert_called_once_with("file:///mnt/data")

    def test_remote_stack_entry_opens_with_gio(self, monkeypatch):
        monitor = _FakeMonitor(
            [_mount("Team Share", path=None, uri="smb://fileserver/team")]
        )
        applet = _applet(monkeypatch, monitor)
        launch = MagicMock()
        monkeypatch.setattr(
            devices_applet_mod.Gio.AppInfo,
            "launch_default_for_uri",
            launch,
        )

        applet.stack_content(32).entries[0].activate()

        launch.assert_called_once_with("smb://fileserver/team", None)

    def test_empty_stack_uses_mounted_devices_message(self, monkeypatch):
        applet = _applet(monkeypatch, _FakeMonitor())

        content = applet.stack_content(32)

        assert content.entries == ()
        assert content.empty_label == "No mounted devices"

    def test_mount_and_unmount_signals_refresh_live_stack(self, monkeypatch):
        monitor = _FakeMonitor([_mount("Data")])
        applet = _applet(monkeypatch, monitor)
        notify = MagicMock()
        applet.start(notify)
        notify.reset_mock()

        monitor.mounts.append(
            _mount("USB Stick", path="/media/USB", uri="file:///media/USB")
        )
        monitor.emit("mount-added")

        assert [entry.label for entry in applet.stack_content(32).entries] == [
            "Data",
            "USB Stick",
        ]
        notify.assert_called_once()

        notify.reset_mock()
        monitor.mounts = []
        monitor.emit("mount-removed")

        assert applet.stack_content(32).entries == ()
        notify.assert_called_once()

    def test_start_is_idempotent_and_stop_disconnects_handlers(self, monkeypatch):
        monitor = _FakeMonitor()
        applet = _applet(monkeypatch, monitor)

        applet.start(MagicMock())
        applet.start(MagicMock())
        applet.stop()

        assert len(monitor.callbacks) == len(devices_applet_mod._MONITOR_SIGNALS)
        assert monitor.disconnected == list(
            range(1, len(devices_applet_mod._MONITOR_SIGNALS) + 1)
        )

    def test_unchanged_monitor_signal_does_not_notify(self, monkeypatch):
        monitor = _FakeMonitor([_mount("Data")])
        applet = _applet(monkeypatch, monitor)
        notify = MagicMock()
        applet.start(notify)

        monitor.emit("volume-changed")

        notify.assert_not_called()

    def test_refresh_menu_reloads_mounts(self, monkeypatch):
        monitor = _FakeMonitor()
        applet = _applet(monkeypatch, monitor)
        applet._refresh_devices = MagicMock()
        callback = []

        class _MenuItem:
            def __init__(self, label: str) -> None:
                self.label = label

            def connect(self, _signal: str, handler) -> None:
                callback.append(handler)

        monkeypatch.setattr(
            devices_applet_mod,
            "Gtk",
            SimpleNamespace(MenuItem=_MenuItem),
        )

        item = applet.get_menu_items()[0]
        callback[0](item)

        assert item.label == "Refresh Devices"
        applet._refresh_devices.assert_called_once()
