"""Tests for the USB Watch applet."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.usbwatch.applet as usbwatch_applet_mod
from docking.applets.usbwatch.applet import UsbWatchApplet
from docking.applets.usbwatch.render import create_usbwatch_icon
from docking.applets.usbwatch.state import mounted_usb_devices, usbwatch_tooltip


class _FakeRoot:
    def __init__(self, path: str | None = "/media/USB", uri: str = "file:///media/USB"):
        self._path = path
        self._uri = uri

    def get_path(self) -> str | None:
        return self._path

    def get_uri(self) -> str:
        return self._uri


class _FakeDrive:
    def __init__(
        self,
        *,
        name: str = "USB Drive",
        removable: bool = True,
        eject: bool = True,
    ) -> None:
        self._name = name
        self._removable = removable
        self._eject = eject
        self.eject_calls = 0

    def get_name(self) -> str:
        return self._name

    def is_removable(self) -> bool:
        return self._removable

    def can_eject(self) -> bool:
        return self._eject

    def eject_with_operation(self, _flags, _operation, _cancellable, callback, data):
        self.eject_calls += 1
        callback(self, object(), data)

    def eject_with_operation_finish(self, _result) -> None:
        return


class _FakeVolume:
    def __init__(self, drive: _FakeDrive | None) -> None:
        self._drive = drive

    def get_drive(self) -> _FakeDrive | None:
        return self._drive


class _FakeMount:
    def __init__(
        self,
        *,
        name: str = "USB Stick",
        root: _FakeRoot | None = None,
        volume: _FakeVolume | None = None,
        unmount: bool = True,
        eject: bool = False,
    ) -> None:
        self._name = name
        self._root = root or _FakeRoot()
        self._volume = volume
        self._unmount = unmount
        self._eject = eject
        self.unmount_calls = 0
        self.eject_calls = 0

    def get_name(self) -> str:
        return self._name

    def get_root(self) -> _FakeRoot:
        return self._root

    def get_volume(self) -> _FakeVolume | None:
        return self._volume

    def can_unmount(self) -> bool:
        return self._unmount

    def can_eject(self) -> bool:
        return self._eject

    def unmount_with_operation(self, _flags, _operation, _cancellable, callback, data):
        self.unmount_calls += 1
        callback(self, object(), data)

    def unmount_with_operation_finish(self, _result) -> None:
        return

    def eject_with_operation(self, _flags, _operation, _cancellable, callback, data):
        self.eject_calls += 1
        callback(self, object(), data)

    def eject_with_operation_finish(self, _result) -> None:
        return


class _FakeMonitor:
    def __init__(self, mounts: list[_FakeMount] | None = None) -> None:
        self._mounts = mounts or []
        self._next_handler_id = 1
        self.connected: list[str] = []
        self.disconnected: list[int] = []

    def get_mounts(self) -> list[_FakeMount]:
        return list(self._mounts)

    def connect(self, signal_name: str, _callback) -> int:
        self.connected.append(signal_name)
        handler_id = self._next_handler_id
        self._next_handler_id += 1
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self.disconnected.append(handler_id)


class _FakeMenuItem:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._sensitive = True
        self._callbacks: list[object] = []

    def get_label(self) -> str:
        return self._label

    def set_sensitive(self, value: bool) -> None:
        self._sensitive = value

    def get_sensitive(self) -> bool:
        return self._sensitive

    def connect(self, _signal: str, callback) -> None:
        self._callbacks.append(callback)


class _FakeSeparatorMenuItem(_FakeMenuItem):
    pass


def _usb_mount(
    *,
    name: str = "USB Stick",
    path: str | None = "/media/USB",
    removable: bool = True,
    drive_eject: bool = True,
    mount_unmount: bool = True,
    mount_eject: bool = False,
) -> tuple[_FakeMount, _FakeDrive]:
    drive = _FakeDrive(removable=removable, eject=drive_eject)
    mount = _FakeMount(
        name=name,
        root=_FakeRoot(path=path),
        volume=_FakeVolume(drive),
        unmount=mount_unmount,
        eject=mount_eject,
    )
    return mount, drive


def test_mounted_usb_devices_filters_removable_and_sorts():
    usb_b, _drive_b = _usb_mount(name="Zulu")
    usb_a, _drive_a = _usb_mount(name="Alpha")
    internal, _drive_internal = _usb_mount(
        name="Internal",
        removable=False,
        drive_eject=False,
    )
    no_volume = _FakeMount(volume=None)
    monitor = _FakeMonitor([usb_b, internal, no_volume, usb_a])

    devices = mounted_usb_devices(monitor)

    assert [device.name for device in devices] == ["Alpha", "Zulu"]
    assert devices[0].mount_path == "/media/USB"
    assert devices[0].can_unmount is True
    assert devices[0].can_eject is True


def test_mounted_usb_devices_uses_uri_when_path_missing():
    mount, _drive = _usb_mount(path=None)
    monitor = _FakeMonitor([mount])

    devices = mounted_usb_devices(monitor)

    assert devices[0].mount_path == "file:///media/USB"


def test_usbwatch_tooltip_empty_and_populated():
    assert "No mounted USB devices" in usbwatch_tooltip([])

    mount, _drive = _usb_mount()
    devices = mounted_usb_devices(_FakeMonitor([mount]))

    tooltip = usbwatch_tooltip(devices)

    assert "1 mounted device" in tooltip
    assert "/media/USB" in tooltip


def test_icon_renders_empty_and_populated():
    assert create_usbwatch_icon(size=48, device_count=0) is not None
    assert create_usbwatch_icon(size=48, device_count=2) is not None


class TestUsbWatchApplet:
    def _fake_gtk(self, monkeypatch):
        monkeypatch.setattr(
            usbwatch_applet_mod,
            "Gtk",
            SimpleNamespace(
                MenuItem=_FakeMenuItem,
                SeparatorMenuItem=_FakeSeparatorMenuItem,
                MountOperation=lambda: object(),
            ),
        )

    def test_menu_empty_state(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        monkeypatch.setattr(
            usbwatch_applet_mod.Gio.VolumeMonitor,
            "get",
            lambda: _FakeMonitor(),
        )

        applet = UsbWatchApplet(48)
        items = applet.get_menu_items()

        assert len(items) == 1
        assert items[0].get_label() == "No mounted USB devices"
        assert items[0].get_sensitive() is False

    def test_menu_lists_devices_and_safe_remove(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        mount, drive = _usb_mount()
        monitor = _FakeMonitor([mount])
        monkeypatch.setattr(
            usbwatch_applet_mod.Gio.VolumeMonitor,
            "get",
            lambda: monitor,
        )

        applet = UsbWatchApplet(48)
        applet.present = MagicMock()
        items = applet.get_menu_items()
        remove_item = next(
            item for item in items if "Safely Remove" in item.get_label()
        )
        remove_item._callbacks[0](remove_item)

        assert mount.unmount_calls == 1
        assert drive.eject_calls == 1
        applet.present.assert_called()

    def test_start_and_stop_volume_monitor_signals(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        monitor = _FakeMonitor()
        monkeypatch.setattr(
            usbwatch_applet_mod.Gio.VolumeMonitor,
            "get",
            lambda: monitor,
        )

        applet = UsbWatchApplet(48)
        applet.start(MagicMock())
        applet.stop()

        assert "mount-added" in monitor.connected
        assert monitor.disconnected == list(range(1, len(monitor.connected) + 1))

    def test_safe_remove_logs_unmount_failure(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        mount, _drive = _usb_mount()
        monitor = _FakeMonitor([mount])
        monkeypatch.setattr(
            usbwatch_applet_mod.Gio.VolumeMonitor,
            "get",
            lambda: monitor,
        )
        monkeypatch.setattr(
            usbwatch_applet_mod.GLib, "Error", RuntimeError, raising=False
        )
        mount.unmount_with_operation_finish = MagicMock(
            side_effect=RuntimeError("busy")
        )

        applet = UsbWatchApplet(48)
        applet.present = MagicMock()
        applet._safe_remove(device=applet._devices[0])

        assert mount.unmount_calls == 1
        applet.present.assert_called()
