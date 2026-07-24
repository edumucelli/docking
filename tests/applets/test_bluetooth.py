"""Tests for Bluetooth applet and state helpers."""

from __future__ import annotations

import logging
import subprocess
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.bluetooth.applet as bluetooth_applet_mod
import docking.applets.bluetooth.state as bluetooth_state_mod
from docking.applets.bluetooth.applet import BluetoothApplet
from docking.applets.bluetooth.render import create_bluetooth_icon
from docking.applets.bluetooth.state import (
    BluetoothAdapterState,
    BluetoothDeviceState,
    BluetoothState,
    BluezBackend,
    adapter_from_state,
    build_tooltip,
    connected_count,
    device_menu_label,
    unavailable_state,
)
from docking.core.config import Config


class _ImmediateThread:
    def __init__(self, target, daemon=True):
        _ = daemon
        self._target = target

    def start(self):
        self._target()


class _ImmediateWorker:
    def __init__(self, *args, **kwargs):
        _ = (args, kwargs)
        self._active_keys: set[str] = set()

    def run(self, *, name, fn, on_result=None, on_error=None):
        _ = name
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return
        if on_result is not None:
            on_result(result)

    def run_guarded(self, *, key, name, fn, on_result=None, on_error=None):
        _ = name
        if key in self._active_keys:
            return False
        self._active_keys.add(key)
        try:
            self.run(name=name, fn=fn, on_result=on_result, on_error=on_error)
        finally:
            self._active_keys.discard(key)
        return True


def _adapter(
    *,
    path: str = "/org/bluez/hci0",
    alias: str = "Adapter 0",
    powered: bool = True,
    discovering: bool = False,
) -> BluetoothAdapterState:
    return BluetoothAdapterState(
        path=path,
        name=alias,
        alias=alias,
        powered=powered,
        discovering=discovering,
        address="AA:BB:CC:DD:EE:FF",
    )


def _device(
    *,
    path: str,
    adapter_path: str = "/org/bluez/hci0",
    alias: str = "Device",
    paired: bool = True,
    trusted: bool = True,
    connected: bool = False,
    battery_percent: int | None = None,
) -> BluetoothDeviceState:
    return BluetoothDeviceState(
        path=path,
        adapter_path=adapter_path,
        name=alias,
        alias=alias,
        address="11:22:33:44:55:66",
        icon_name="audio-headphones",
        paired=paired,
        trusted=trusted,
        connected=connected,
        battery_percent=battery_percent,
        rssi=None,
    )


def _state(**overrides: object) -> BluetoothState:
    base = BluetoothState(
        available=True,
        adapters=(_adapter(),),
        devices=(),
        active_adapter_path="/org/bluez/hci0",
        error="",
    )
    values = {name: getattr(base, name) for name in BluetoothState.__dataclass_fields__}
    values.update(overrides)
    return BluetoothState(**values)


class TestBluetoothStateHelpers:
    def test_connected_count(self):
        state = _state(
            devices=(
                _device(path="/d/1", connected=True),
                _device(path="/d/2", connected=False),
            )
        )
        assert connected_count(state) == 1

    def test_adapter_from_state_prefers_selected(self):
        state = _state(
            adapters=(
                _adapter(path="/org/bluez/hci0", alias="A"),
                _adapter(path="/org/bluez/hci1", alias="B"),
            )
        )
        assert adapter_from_state(state, "/org/bluez/hci1") == "/org/bluez/hci1"

    def test_adapter_from_state_falls_back_to_powered(self):
        state = _state(
            adapters=(
                _adapter(path="/org/bluez/hci0", alias="A", powered=False),
                _adapter(path="/org/bluez/hci1", alias="B", powered=True),
            )
        )
        assert adapter_from_state(state, "/invalid") == "/org/bluez/hci1"

    def test_build_tooltip_includes_counts_and_discovery(self):
        state = _state(
            adapters=(_adapter(discovering=True),),
            devices=(
                _device(path="/d/1", paired=True, connected=True, battery_percent=80),
                _device(path="/d/2", paired=True, connected=False),
            ),
        )
        text = build_tooltip(state, "/org/bluez/hci0")
        assert "Bluetooth: On" in text
        assert "Connected: 1" in text
        assert "Paired: 2" in text
        assert "Discovering: Yes" in text
        assert "Battery:" in text

    def test_build_tooltip_unavailable(self):
        assert "No adapter" in build_tooltip(unavailable_state(), "")

    def test_device_menu_label(self):
        label = device_menu_label(
            _device(path="/d", connected=True, paired=True, battery_percent=77)
        )
        assert "Connected" in label
        assert "Paired" in label
        assert "77%" in label

    def test_adapter_from_state_edge_cases(self):
        assert adapter_from_state(_state(adapters=()), None) is None
        state = _state(
            adapters=(
                _adapter(path="/org/bluez/hci0", alias="A", powered=False),
                _adapter(path="/org/bluez/hci1", alias="B", powered=False),
            )
        )
        assert adapter_from_state(state, None) == "/org/bluez/hci0"

    def test_build_tooltip_error_and_missing_adapter(self):
        assert build_tooltip(unavailable_state(error="service down"), "") == (
            "Bluetooth: service down"
        )
        assert (
            build_tooltip(_state(), "/org/bluez/unknown")
            == "Bluetooth: No adapter/service"
        )

    def test_device_menu_label_without_tags(self):
        label = device_menu_label(
            _device(
                path="/d", alias="", paired=False, connected=False, battery_percent=None
            )
        )
        assert label == "11:22:33:44:55:66"

    def test_command_helpers_prefer_first_available(self, monkeypatch):
        commands = {
            "blueman-sendto": None,
            "bluetooth-sendto": "/usr/bin/bluetooth-sendto",
            "blueman-manager": None,
            "gnome-control-center": "/usr/bin/gnome-control-center",
            "blueman-adapters": None,
            "blueman-services": "/usr/bin/blueman-services",
        }

        monkeypatch.setattr(
            bluetooth_state_mod.shutil,
            "which",
            lambda cmd: commands.get(cmd),
        )

        assert bluetooth_state_mod.send_files_command() == ["bluetooth-sendto"]
        assert bluetooth_state_mod.devices_command() == [
            "gnome-control-center",
            "bluetooth",
        ]
        assert bluetooth_state_mod.adapters_command() == [
            "gnome-control-center",
            "bluetooth",
        ]
        assert bluetooth_state_mod.local_services_command() == ["blueman-services"]

    def test_open_command_helpers(self, monkeypatch):
        launched: list[list[str]] = []

        monkeypatch.setattr(
            bluetooth_state_mod,
            "send_files_command",
            lambda: ["bluetooth-sendto"],
        )
        monkeypatch.setattr(
            bluetooth_state_mod,
            "devices_command",
            lambda: ["gnome-control-center", "bluetooth"],
        )
        monkeypatch.setattr(
            bluetooth_state_mod,
            "adapters_command",
            lambda: ["blueman-adapters"],
        )
        monkeypatch.setattr(
            bluetooth_state_mod,
            "local_services_command",
            lambda: ["blueman-services"],
        )
        monkeypatch.setattr(
            bluetooth_state_mod.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(list(cmd)),
        )

        assert bluetooth_state_mod.open_send_files() is True
        assert bluetooth_state_mod.open_devices() is True
        assert bluetooth_state_mod.open_adapters() is True
        assert bluetooth_state_mod.open_local_services() is True
        assert launched == [
            ["bluetooth-sendto"],
            ["gnome-control-center", "bluetooth"],
            ["blueman-adapters"],
            ["blueman-services"],
        ]


class TestBluezBackend:
    def test_pair_device_uses_bluetoothctl_fallback_on_failure(self):
        backend = object.__new__(BluezBackend)
        backend._call_method = lambda **kwargs: False  # type: ignore[attr-defined]
        backend._pair_with_bluetoothctl = (  # type: ignore[attr-defined]
            lambda **kwargs: True
        )
        assert backend.pair_device("/org/bluez/hci0/dev_x", address="AA:BB") is True

    def test_set_adapter_power_off_stops_discovery_and_retries(self, monkeypatch):
        backend = object.__new__(BluezBackend)
        calls: list[str] = []
        attempts = [False, True]
        backend.stop_discovery = (  # type: ignore[attr-defined]
            lambda path, quiet=False: calls.append(f"stop:{path}:{quiet}") or True
        )
        backend._wait_for_discovery_state = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("wait") or True
        )
        backend._set_power_with_bluetoothctl = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("fallback") or False
        )
        backend._disconnect_connected_devices = (  # type: ignore[attr-defined]
            lambda adapter_path: calls.append(f"disconnect:{adapter_path}")
        )
        backend._get_adapter_props = lambda **kwargs: {  # type: ignore[attr-defined]
            "Discovering": False
        }
        backend._set_property = lambda **kwargs: calls.append("set") or attempts.pop(0)  # type: ignore[attr-defined]
        monkeypatch.setattr(bluetooth_state_mod.time, "sleep", lambda _s: None)

        assert backend.set_adapter_power("/org/bluez/hci0", False) is True
        assert calls[0] == "stop:/org/bluez/hci0:True"
        assert "wait" in calls
        assert calls.count("set") == 2
        assert "disconnect:/org/bluez/hci0" not in calls
        assert "fallback" not in calls

    def test_set_adapter_power_off_disconnects_on_repeated_failure(self, monkeypatch):
        backend = object.__new__(BluezBackend)
        calls: list[str] = []
        attempts = [False, False, False, False, False, False, False, False]
        backend.stop_discovery = (  # type: ignore[attr-defined]
            lambda path, quiet=False: calls.append(f"stop:{path}:{quiet}") or True
        )
        backend._wait_for_discovery_state = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("wait") or True
        )
        backend._set_power_with_bluetoothctl = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("fallback") or True
        )
        backend._disconnect_connected_devices = (  # type: ignore[attr-defined]
            lambda adapter_path: calls.append(f"disconnect:{adapter_path}")
        )
        backend._get_adapter_props = lambda **kwargs: {  # type: ignore[attr-defined]
            "Discovering": False
        }
        backend._set_property = lambda **kwargs: calls.append("set") or attempts.pop(0)  # type: ignore[attr-defined]
        monkeypatch.setattr(bluetooth_state_mod.time, "sleep", lambda _s: None)

        assert backend.set_adapter_power("/org/bluez/hci0", False) is True
        assert "disconnect:/org/bluez/hci0" in calls
        assert calls.count("set") == 8
        assert calls.count("wait") == 2
        assert "fallback" in calls

    def test_set_adapter_power_on_uses_fallback_when_needed(self):
        backend = object.__new__(BluezBackend)
        backend._set_property = lambda **kwargs: False  # type: ignore[attr-defined]
        backend._set_power_with_bluetoothctl = lambda **kwargs: True  # type: ignore[attr-defined]
        assert backend.set_adapter_power("/org/bluez/hci0", True) is True

    def test_set_adapter_power_off_fails_fast_when_discovery_is_external(
        self, monkeypatch
    ):
        backend = object.__new__(BluezBackend)
        calls: list[str] = []
        backend.stop_discovery = (  # type: ignore[attr-defined]
            lambda path, quiet=False: calls.append(f"stop:{path}:{quiet}") or True
        )
        backend._wait_for_discovery_state = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("wait") or False
        )
        backend._get_adapter_props = lambda **kwargs: {  # type: ignore[attr-defined]
            "Discovering": True
        }
        backend._set_property = lambda **kwargs: calls.append("set") or False  # type: ignore[attr-defined]
        backend._set_power_with_bluetoothctl = (  # type: ignore[attr-defined]
            lambda **kwargs: calls.append("fallback") or False
        )
        backend._disconnect_connected_devices = lambda **kwargs: calls.append(
            "disconnect"
        )  # type: ignore[attr-defined]
        monkeypatch.setattr(bluetooth_state_mod.time, "sleep", lambda _s: None)

        assert backend.set_adapter_power("/org/bluez/hci0", False) is False
        assert calls.count("set") == 1
        assert "disconnect" not in calls
        assert "fallback" in calls

    def test_get_state_unavailable_and_success_paths(self):
        backend = object.__new__(BluezBackend)
        backend._has_bluez_owner = lambda: False  # type: ignore[attr-defined]
        state = backend.get_state()
        assert state.available is False
        assert "unavailable" in state.error.lower()

        backend._has_bluez_owner = lambda: True  # type: ignore[attr-defined]
        backend._get_managed_objects = lambda: None  # type: ignore[attr-defined]
        state = backend.get_state()
        assert state.available is False
        assert "query failed" in state.error.lower()

        backend._get_managed_objects = dict  # type: ignore[attr-defined]
        state = backend.get_state()
        assert state.available is False
        assert "no bluetooth adapter" in state.error.lower()

        backend._get_managed_objects = lambda: {  # type: ignore[attr-defined]
            "/org/bluez/hci0": {
                bluetooth_state_mod.ADAPTER_IFACE: {
                    "Name": "hci0",
                    "Alias": "Adapter 0",
                    "Powered": True,
                }
            }
        }
        state = backend.get_state(active_adapter_path="/org/bluez/hci0")
        assert state.available is True
        assert state.active_adapter_path == "/org/bluez/hci0"

    def test_backend_wrapper_methods_call_internal_helpers(self):
        backend = object.__new__(BluezBackend)
        calls: list[tuple[str, str]] = []
        backend._call_method = lambda **kwargs: (
            calls.append(  # type: ignore[attr-defined]
                (kwargs["interface"], kwargs["method"])
            )
            or True
        )
        backend._set_property = lambda **kwargs: (
            calls.append(  # type: ignore[attr-defined]
                (kwargs["interface"], kwargs["property_name"])
            )
            or True
        )
        assert backend.start_discovery("/a") is True
        assert backend.stop_discovery("/a") is True
        assert backend.connect_device("/d") is True
        assert backend.disconnect_device("/d") is True
        assert backend.remove_device("/a", "/d") is True
        assert backend.set_trusted("/d", True) is True
        assert (bluetooth_state_mod.ADAPTER_IFACE, "StartDiscovery") in calls
        assert (bluetooth_state_mod.ADAPTER_IFACE, "StopDiscovery") in calls
        assert (bluetooth_state_mod.DEVICE_IFACE, "Connect") in calls
        assert (bluetooth_state_mod.DEVICE_IFACE, "Disconnect") in calls
        assert (bluetooth_state_mod.ADAPTER_IFACE, "RemoveDevice") in calls
        assert (bluetooth_state_mod.DEVICE_IFACE, "Trusted") in calls

    def test_has_owner_and_get_managed_objects_branches(self, monkeypatch):
        backend = object.__new__(BluezBackend)
        backend._dbus_proxy = None
        assert backend._has_bluez_owner() is False

        class _Result:
            def __init__(self, payload):
                self._payload = payload

            def unpack(self):
                return self._payload

        class _Proxy:
            def call_sync(self, *_args, **_kwargs):
                return _Result((True,))

        backend._dbus_proxy = _Proxy()
        assert backend._has_bluez_owner() is True

        class _ProxyFail:
            def call_sync(self, *_args, **_kwargs):
                raise Exception("dbus")

        backend._dbus_proxy = _ProxyFail()
        monkeypatch.setattr(bluetooth_state_mod.GLib, "Error", Exception)
        assert backend._has_bluez_owner() is False

        backend._bus = None
        assert backend._get_managed_objects() is None

        class _BusFail:
            def call_sync(self, *_args, **_kwargs):
                raise Exception("boom")

        backend._bus = _BusFail()
        assert backend._get_managed_objects() is None

        class _BusEmpty:
            def call_sync(self, *_args, **_kwargs):
                return _Result(())

        backend._bus = _BusEmpty()
        assert backend._get_managed_objects() == {}

    def test_call_method_and_set_property_branches(self, monkeypatch):
        backend = object.__new__(BluezBackend)
        backend._bus = None
        assert (
            backend._call_method(path="/x", interface="i", method="M", parameters=None)
            is False
        )
        assert (
            backend._set_property(
                path="/x",
                interface="i",
                property_name="P",
                signature="s",
                value="v",
            )
            is False
        )

        class _Bus:
            def __init__(self, error_text: str | None = None):
                self.error_text = error_text

            def call_sync(self, *_args, **_kwargs):
                if self.error_text is not None:
                    raise Exception(self.error_text)
                return object()

        monkeypatch.setattr(bluetooth_state_mod.GLib, "Error", Exception)
        backend._bus = _Bus()
        assert (
            backend._call_method(path="/x", interface="i", method="M", parameters=None)
            is True
        )
        backend._bus = _Bus("org.bluez.Error.NotReady")
        assert (
            backend._call_method(
                path="/x",
                interface="i",
                method="M",
                parameters=None,
                tolerate_errors=("org.bluez.Error.NotReady",),
            )
            is True
        )
        assert (
            backend._call_method(path="/x", interface="i", method="M", parameters=None)
            is False
        )
        assert (
            backend._set_property(
                path="/x",
                interface="i",
                property_name="P",
                signature="s",
                value="v",
                quiet=True,
            )
            is False
        )

    def test_bluetoothctl_helpers(self, monkeypatch):
        backend = object.__new__(BluezBackend)
        monkeypatch.setattr(bluetooth_state_mod.shutil, "which", lambda cmd: None)
        assert backend._pair_with_bluetoothctl(address="", timeout_s=1) is False
        assert backend._set_power_with_bluetoothctl(powered=True) is False

        monkeypatch.setattr(bluetooth_state_mod.shutil, "which", lambda cmd: "/bin/bt")

        def timeout_run(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(cmd="bluetoothctl", timeout=1)

        monkeypatch.setattr(bluetooth_state_mod.subprocess, "run", timeout_run)
        assert backend._pair_with_bluetoothctl(address="AA:BB", timeout_s=1) is False
        assert backend._set_power_with_bluetoothctl(powered=False) is False

        class _Result:
            def __init__(self, returncode=0, stdout="", stderr=""):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        monkeypatch.setattr(
            bluetooth_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Result(returncode=0, stdout="Failed"),
        )
        assert backend._pair_with_bluetoothctl(address="AA:BB", timeout_s=1) is False
        assert backend._set_power_with_bluetoothctl(powered=False) is False

        monkeypatch.setattr(
            bluetooth_state_mod.subprocess,
            "run",
            lambda *args, **kwargs: _Result(returncode=0, stdout="ok"),
        )
        assert backend._pair_with_bluetoothctl(address="AA:BB", timeout_s=1) is True
        assert backend._set_power_with_bluetoothctl(powered=True) is True

    def test_disconnect_wait_and_adapter_props_helpers(self, monkeypatch):
        backend = object.__new__(BluezBackend)
        disconnected: list[str] = []
        backend._call_method = lambda **kwargs: (
            disconnected.append(kwargs["path"]) or True
        )  # type: ignore[attr-defined]
        backend._get_managed_objects = lambda: {  # type: ignore[attr-defined]
            "/d1": {
                bluetooth_state_mod.DEVICE_IFACE: {
                    "Adapter": "/a0",
                    "Connected": True,
                }
            },
            "/d2": {
                bluetooth_state_mod.DEVICE_IFACE: {
                    "Adapter": "/a1",
                    "Connected": True,
                }
            },
            "/d3": {
                bluetooth_state_mod.DEVICE_IFACE: {
                    "Adapter": "/a0",
                    "Connected": False,
                }
            },
        }
        backend._disconnect_connected_devices(adapter_path="/a0")
        assert disconnected == ["/d1"]

        backend._get_managed_objects = lambda: None  # type: ignore[attr-defined]
        assert backend._get_adapter_props(adapter_path="/a0") is None

        backend._get_managed_objects = lambda: {"/a0": "bad"}  # type: ignore[attr-defined]
        assert backend._get_adapter_props(adapter_path="/a0") is None

        backend._get_managed_objects = lambda: {  # type: ignore[attr-defined]
            "/a0": {bluetooth_state_mod.ADAPTER_IFACE: {"Discovering": True}}
        }
        assert backend._get_adapter_props(adapter_path="/a0") == {"Discovering": True}

        backend._get_adapter_props = lambda **kwargs: {"Discovering": False}  # type: ignore[attr-defined]
        monkeypatch.setattr(bluetooth_state_mod.time, "sleep", lambda _s: None)
        times = iter([0.0, 0.01])
        monkeypatch.setattr(bluetooth_state_mod.time, "monotonic", lambda: next(times))
        assert (
            backend._wait_for_discovery_state(
                adapter_path="/a0",
                target_discovering=False,
                timeout_s=1.0,
            )
            is True
        )

        backend._get_adapter_props = lambda **kwargs: None  # type: ignore[attr-defined]
        times = iter([0.0, 0.01])
        monkeypatch.setattr(bluetooth_state_mod.time, "monotonic", lambda: next(times))
        assert (
            backend._wait_for_discovery_state(
                adapter_path="/a0",
                target_discovering=False,
                timeout_s=1.0,
            )
            is False
        )

    def test_parse_objects_and_helper_conversions(self):
        objects = {
            "/org/bluez/hci1": {
                bluetooth_state_mod.ADAPTER_IFACE: {
                    "Name": "hci1",
                    "Alias": "",
                    "Powered": True,
                    "Discovering": False,
                    "Address": "AA",
                }
            },
            "/org/bluez/hci0": {
                bluetooth_state_mod.ADAPTER_IFACE: {
                    "Name": "",
                    "Alias": "",
                    "Powered": False,
                    "Discovering": True,
                    "Address": "BB",
                }
            },
            "/dev/a": {
                bluetooth_state_mod.DEVICE_IFACE: {
                    "Adapter": "/org/bluez/hci0",
                    "Name": "A",
                    "Alias": "A",
                    "Address": "11",
                    "Icon": "audio",
                    "Paired": True,
                    "Trusted": True,
                    "Connected": True,
                    "RSSI": "-20",
                },
                bluetooth_state_mod.BATTERY_IFACE: {"Percentage": 88},
            },
            "/dev/b": {
                bluetooth_state_mod.DEVICE_IFACE: {
                    "Adapter": "/org/bluez/hci0",
                    "Name": "B",
                    "Alias": "B",
                    "Address": "22",
                    "Icon": "",
                    "Paired": False,
                    "Trusted": False,
                    "Connected": False,
                    "RSSI": "bad",
                }
            },
            "/bad": "bad",
        }
        adapters, devices = bluetooth_state_mod._parse_objects(objects=objects)
        assert [a.path for a in adapters] == ["/org/bluez/hci0", "/org/bluez/hci1"]
        assert devices[0].path == "/dev/a"
        assert devices[0].battery_percent == 88
        assert devices[1].rssi is None
        state = _state(adapters=tuple(adapters), devices=tuple(devices))
        assert (
            bluetooth_state_mod._find_adapter(state=state, path="/org/bluez/hci0")
            is not None
        )
        assert bluetooth_state_mod._find_adapter(state=state, path="/missing") is None

        nameless = _device(path="/d/x", alias="")
        nameless = replace(nameless, name="", address="", path="/d/name")
        assert bluetooth_state_mod._device_display_name(nameless) == "name"

        class _Variant:
            def __init__(self, value):
                self._value = value

            def unpack(self):
                return self._value

        class _BrokenVariant:
            def unpack(self):
                raise RuntimeError("bad")

        assert bluetooth_state_mod._unpack({"k": _Variant([1, _Variant(2)])}) == {
            "k": [1, 2]
        }
        broken = _BrokenVariant()
        assert bluetooth_state_mod._unpack(broken) is broken
        assert bluetooth_state_mod._as_str(None) == ""
        assert bluetooth_state_mod._as_str(_Variant("x")) == "x"
        assert bluetooth_state_mod._as_bool(_Variant(True)) is True
        assert bluetooth_state_mod._as_bool("true") is False
        assert bluetooth_state_mod._as_int(_Variant("7")) == 7
        assert bluetooth_state_mod._as_int("bad", default=None) is None

    def test_as_int_skips_debug_log_for_absent_optional_values(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="docking.bluetooth"):
            assert bluetooth_state_mod._as_int(None, default=None) is None
            assert bluetooth_state_mod._as_int("  ", default=None) is None

        assert "Failed to coerce Bluetooth value" not in caplog.text


class _StubBackend:
    def __init__(self, initial_state: BluetoothState) -> None:
        self.state = initial_state
        self.power_calls: list[tuple[str, bool]] = []
        self.discovery_calls: list[str] = []

    def get_state(self, active_adapter_path: str | None = None) -> BluetoothState:
        if active_adapter_path:
            self.state = replace(self.state, active_adapter_path=active_adapter_path)
        return self.state

    def set_adapter_power(self, adapter_path: str, powered: bool) -> bool:
        self.power_calls.append((adapter_path, powered))
        adapters = tuple(
            replace(adapter, powered=powered)
            if adapter.path == adapter_path
            else adapter
            for adapter in self.state.adapters
        )
        self.state = replace(self.state, adapters=adapters)
        return True

    def start_discovery(self, adapter_path: str) -> bool:
        self.discovery_calls.append(adapter_path)
        return True

    def stop_discovery(self, adapter_path: str, quiet: bool = False) -> bool:
        _ = quiet
        return True

    def connect_device(self, device_path: str) -> bool:
        _ = device_path
        return True

    def disconnect_device(self, device_path: str) -> bool:
        _ = device_path
        return True

    def pair_device(
        self, device_path: str, address: str = "", timeout_s: int = 20
    ) -> bool:
        _ = (device_path, address, timeout_s)
        return True

    def remove_device(self, adapter_path: str, device_path: str) -> bool:
        _ = (adapter_path, device_path)
        return True

    def set_trusted(self, device_path: str, trusted: bool) -> bool:
        _ = (device_path, trusted)
        return True


def _make_applet(
    monkeypatch,
    state: BluetoothState,
) -> tuple[BluetoothApplet, _StubBackend]:
    backend = _StubBackend(initial_state=state)
    monkeypatch.setattr(bluetooth_applet_mod, "BluezBackend", lambda: backend)
    monkeypatch.setattr(bluetooth_applet_mod, "BackgroundWorker", _ImmediateWorker)
    applet = BluetoothApplet(48, config=Config())
    applet._on_poll_result(
        backend.get_state(active_adapter_path=applet._active_adapter_path)
    )
    backend.discovery_calls.clear()
    return applet, backend


class _FakeMenu:
    def __init__(self) -> None:
        self.children: list[object] = []

    def append(self, item) -> None:
        self.children.append(item)


class _FakeMenuItem:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._sensitive = True
        self._submenu = None
        self._signals: dict[str, list[object]] = {}

    def get_label(self) -> str:
        return self._label

    def set_sensitive(self, value: bool) -> None:
        self._sensitive = value

    def get_sensitive(self) -> bool:
        return self._sensitive

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu


class _FakeCheckMenuItem(_FakeMenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label)
        self._active = False

    def set_active(self, active: bool) -> None:
        self._active = active

    def get_active(self) -> bool:
        return self._active


class _FakeRadioMenuItem(_FakeCheckMenuItem):
    def join_group(self, first) -> None:
        _ = first


class _FakeSeparatorMenuItem(_FakeMenuItem):
    def __init__(self) -> None:
        super().__init__(label="")


class TestBluetoothApplet:
    def test_constructor_does_not_query_bluez_synchronously(self, monkeypatch):
        class _Backend:
            def __init__(self) -> None:
                self.get_state_calls = 0

            def get_state(
                self, active_adapter_path: str | None = None
            ) -> BluetoothState:
                _ = active_adapter_path
                self.get_state_calls += 1
                return _state()

        backend = _Backend()
        monkeypatch.setattr(bluetooth_applet_mod, "BluezBackend", lambda: backend)
        monkeypatch.setattr(bluetooth_applet_mod, "BackgroundWorker", _ImmediateWorker)

        applet = BluetoothApplet(48, config=Config())

        assert backend.get_state_calls == 0
        assert applet._state.available is False

    def _fake_gtk(self, monkeypatch):
        fake_gtk = SimpleNamespace(
            Menu=_FakeMenu,
            MenuItem=_FakeMenuItem,
            CheckMenuItem=_FakeCheckMenuItem,
            RadioMenuItem=_FakeRadioMenuItem,
            SeparatorMenuItem=_FakeSeparatorMenuItem,
        )
        monkeypatch.setattr(bluetooth_applet_mod, "Gtk", fake_gtk)

    def test_creates_with_icon(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        assert applet.item.icon is not None

    def test_click_toggles_adapter_power(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state())
        applet._set_power_async = lambda *, target: backend.set_adapter_power(  # type: ignore[assignment]
            "/org/bluez/hci0",
            target,
        )
        applet.on_clicked()
        assert backend.power_calls
        assert backend.power_calls[0] == ("/org/bluez/hci0", False)

    def test_power_off_temporarily_suppresses_discovery(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._on_power_result = lambda *args: False  # type: ignore[assignment]
        monkeypatch.setattr(bluetooth_applet_mod.time, "monotonic", lambda: 100.0)
        applet.on_clicked()
        assert applet._suppress_discovery_until > 100.0

    def test_power_result_sets_busy_error_message(self, monkeypatch):
        applet, _backend = _make_applet(
            monkeypatch,
            _state(adapters=(_adapter(discovering=True),)),
        )
        applet._power_transition_in_progress = True
        applet.present = lambda: None  # type: ignore[assignment]

        applet._on_power_result(
            False, False, _state(adapters=(_adapter(discovering=True),))
        )

        assert "blocked" in applet._action_error.lower()
        assert applet._power_transition_in_progress is False

    def test_menu_contains_expected_sections(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        monkeypatch.setattr(
            bluetooth_applet_mod, "send_files_command", lambda: ["bluetooth-sendto"]
        )
        monkeypatch.setattr(
            bluetooth_applet_mod,
            "devices_command",
            lambda: ["gnome-control-center", "bluetooth"],
        )
        monkeypatch.setattr(
            bluetooth_applet_mod,
            "adapters_command",
            lambda: ["gnome-control-center", "bluetooth"],
        )
        monkeypatch.setattr(
            bluetooth_applet_mod, "local_services_command", lambda: ["blueman-services"]
        )
        state = _state(
            adapters=(
                _adapter(path="/org/bluez/hci0", alias="A0"),
                _adapter(path="/org/bluez/hci1", alias="A1"),
            ),
            devices=(
                _device(path="/d/1", connected=True, paired=True),
                _device(path="/d/2", connected=False, paired=True),
                _device(path="/d/3", connected=False, paired=False),
            ),
        )
        applet, _backend = _make_applet(monkeypatch, state)
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "Turn Bluetooth Off" in labels
        assert "Disconnect Device" in labels
        assert "Send Files to Device..." in labels
        assert "Recent Connections" in labels
        assert "Devices..." in labels
        assert "Adapters..." in labels
        assert "Local Services..." in labels
        assert "Continuous Discovery" in labels
        assert "Adapter" in labels
        assert "Connected Devices" in labels
        assert "Paired Devices" in labels
        assert "Discovered Devices" in labels
        assert "Refresh Now" in labels

        assert labels.index("Connected Devices") < labels.index("Paired Devices")
        assert labels.index("Paired Devices") < labels.index("Discovered Devices")

    def test_empty_groups_do_not_add_none_entries(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        applet, _backend = _make_applet(monkeypatch, _state(devices=()))
        labels = [
            item.get_label() for item in applet.get_menu_items() if item.get_label()
        ]
        assert "None" not in labels
        assert "Connected Devices" not in labels
        assert "Paired Devices" not in labels
        assert "Discovered Devices" not in labels

    def test_recent_connections_submenu_prefers_recent_paired_devices(
        self, monkeypatch
    ):
        self._fake_gtk(monkeypatch)
        applet, _backend = _make_applet(
            monkeypatch,
            _state(
                devices=(
                    _device(
                        path="/d/1",
                        alias="Headphones",
                        connected=False,
                        paired=True,
                    ),
                    _device(
                        path="/d/2",
                        alias="Keyboard",
                        connected=True,
                        paired=True,
                    ),
                ),
            ),
        )
        applet._recent_connections = {
            "11:22:33:44:55:66": 100.0,
        }
        submenu_item = applet._build_recent_connections_submenu(
            adapter_path="/org/bluez/hci0"
        )
        submenu = submenu_item.get_submenu()
        assert submenu is not None
        labels = [item.get_label() for item in submenu.children]
        assert labels == ["Keyboard (Connected, Paired)", "Headphones (Paired)"]
        assert submenu.children[0].get_sensitive() is False

    def test_record_recent_connections_persists_only_on_new_connection(
        self, monkeypatch
    ):
        applet, _backend = _make_applet(monkeypatch, _state(devices=()))
        saved: list[dict[str, object]] = []
        applet.save_prefs = lambda prefs: saved.append(prefs)  # type: ignore[assignment]
        monkeypatch.setattr(bluetooth_applet_mod.time, "time", lambda: 123.0)

        state = _state(devices=(_device(path="/d/1", connected=True, paired=True),))
        applet._record_recent_connections(state)
        assert saved
        assert applet._recent_connections["11:22:33:44:55:66"] == 123.0

        saved.clear()
        applet._record_recent_connections(state)
        assert saved == []

    def test_scroll_is_noop(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet.on_scroll(direction_up=True)

    def test_start_and_stop_manage_poll_and_discovery_timers(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        timeout_calls: list[int] = []
        removed: list[int] = []
        applet._ensure_discovery = lambda: timeout_calls.append(99)  # type: ignore[assignment]
        applet._stop_local_discovery = lambda quiet=True: True  # type: ignore[assignment]
        monkeypatch.setattr(
            bluetooth_applet_mod.GLib,
            "timeout_add_seconds",
            lambda sec, cb: 10 if sec == bluetooth_applet_mod.POLL_INTERVAL_S else 20,
        )
        monkeypatch.setattr(
            bluetooth_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )

        applet.start(notify=lambda: None)
        assert applet._poll_id == 10
        assert applet._discovery_id == 20
        assert timeout_calls == [99]

        applet.stop()
        assert applet._poll_id == 0
        assert applet._discovery_id == 0
        assert removed == [10, 20]

    def test_menu_handles_unavailable_and_missing_active_adapter(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        applet, _backend = _make_applet(monkeypatch, unavailable_state())
        items = applet.get_menu_items()
        assert items[0].get_label() == "Bluetooth unavailable"

        state = _state(
            adapters=(_adapter(path="/org/bluez/hci0"),), active_adapter_path=""
        )
        applet2, _backend2 = _make_applet(monkeypatch, state)
        applet2._active_adapter_path = "/missing"
        items = applet2.get_menu_items()
        assert items[0].get_label() == "No Bluetooth adapter"

    def test_make_header_creates_insensitive_item(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        header = BluetoothApplet._make_header("Section")
        assert header.get_label() == "Section"
        assert header.get_sensitive() is False

    def test_tick_and_discovery_tick(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        ticked: list[str] = []
        applet._worker.run_guarded = lambda **kwargs: ticked.append("poll") or True  # type: ignore[method-assign]
        applet._ensure_discovery = lambda: ticked.append("discover")  # type: ignore[assignment]

        assert applet._tick() is True
        assert applet._discovery_tick() is True
        assert ticked == ["poll", "discover"]

    def test_tick_and_refresh_now_schedule_result(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state())
        results: list[BluetoothState] = []
        applet._on_poll_result = lambda state: results.append(state) or False  # type: ignore[assignment]

        applet._tick()
        assert results and results[0].available is True

        applet._refresh_now()
        assert results[-1] == backend.get_state(
            active_adapter_path=applet._active_adapter_path
        )

    def test_on_poll_result_syncs_adapter_and_resets_local_discovery(self, monkeypatch):
        state = _state(adapters=(_adapter(path="/org/bluez/hci0", discovering=False),))
        applet, _backend = _make_applet(monkeypatch, state)
        applet._local_discovery_active = True
        applet._ensure_discovery = lambda: None  # type: ignore[assignment]
        applet.present = lambda: None  # type: ignore[assignment]
        assert applet._on_poll_result(state) is False
        assert applet._local_discovery_active is False

    def test_on_poll_result_skips_present_when_state_is_unchanged(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        state = applet._state
        applet._local_discovery_active = False
        applet._ensure_discovery = lambda: None  # type: ignore[assignment]
        present = MagicMock()
        applet.present = present  # type: ignore[method-assign]

        assert applet._on_poll_result(state) is False

        present.assert_not_called()

    def test_sync_selected_adapter_active_adapter_and_discovery_flow(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state())
        monkeypatch.setattr(
            bluetooth_applet_mod.GLib,
            "idle_add",
            lambda func, started: func(started),
        )
        saved: list[dict[str, object]] = []
        applet.save_prefs = lambda prefs: saved.append(prefs)  # type: ignore[assignment]
        applet._active_adapter_path = "/invalid"
        applet._sync_selected_adapter()
        assert applet._active_adapter_path == "/org/bluez/hci0"
        assert saved

        saved.clear()
        applet._sync_selected_adapter()
        assert saved == []

        applet._state = _state(adapters=())
        assert applet._active_adapter() is None

        applet._state = _state(adapters=(_adapter(powered=False),))
        applet._continuous_discovery = True
        applet._ensure_discovery()
        assert backend.discovery_calls == []

        applet._state = _state(adapters=(_adapter(powered=True, discovering=False),))
        applet._continuous_discovery = False
        applet._ensure_discovery()
        assert backend.discovery_calls == []

        applet._continuous_discovery = True
        monkeypatch.setattr(bluetooth_applet_mod.time, "monotonic", lambda: 100.0)
        applet._suppress_discovery_until = 120.0
        applet._ensure_discovery()
        assert backend.discovery_calls == []

        applet._suppress_discovery_until = 0.0
        applet._ensure_discovery()
        assert backend.discovery_calls == ["/org/bluez/hci0"]
        assert applet._local_discovery_active is True

    def test_run_async_and_connect_device_paths(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state())
        results: list[BluetoothState] = []
        applet._on_poll_result = lambda state: results.append(state) or False  # type: ignore[assignment]

        applet._run_async(lambda: True)
        assert results

        backend.connect_device = (  # type: ignore[method-assign]
            lambda device_path: device_path == "/d/ok"
        )
        backend.pair_device = (  # type: ignore[method-assign]
            lambda device_path, address="", timeout_s=20: True
        )
        assert applet._connect_device(_device(path="/d/ok", paired=True)) is True
        assert applet._connect_device(_device(path="/d/new", paired=False)) is False

    def test_select_adapter_power_toggle_and_power_async(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state())
        refreshed: list[str] = []
        applet._refresh_now = lambda: refreshed.append("refresh")  # type: ignore[assignment]
        saved: list[dict[str, object]] = []
        applet.save_prefs = lambda prefs: saved.append(prefs)  # type: ignore[assignment]

        class _Radio:
            def __init__(self, active: bool):
                self._active = active

            def get_active(self):
                return self._active

        applet._on_select_adapter(_Radio(active=False), "/org/bluez/hci0")
        applet._on_select_adapter(_Radio(active=True), "/org/bluez/hci0")
        assert refreshed == ["refresh"]
        assert saved

        calls: list[bool] = []
        applet._set_power_async = lambda *, target: calls.append(target)  # type: ignore[assignment]

        class _Check:
            def __init__(self, active: bool):
                self._active = active
                self.reverted = None

            def get_active(self):
                return self._active

            def set_active(self, value: bool):
                self.reverted = value

        applet._power_transition_in_progress = True
        widget = _Check(active=False)
        applet._on_power_toggled(widget)
        assert widget.reverted is True
        applet._power_transition_in_progress = False

        applet._state = _state(adapters=(_adapter(powered=True),))
        applet._on_power_toggled(_Check(active=True))
        assert calls == []
        applet._on_power_toggled(_Check(active=False))
        assert calls == [False]

        applet._set_power_async = (
            bluetooth_applet_mod.BluetoothApplet._set_power_async.__get__(  # type: ignore[method-assign]
                applet,
                bluetooth_applet_mod.BluetoothApplet,
            )
        )

        applet._power_transition_in_progress = True
        applet._set_power_async(target=True)
        assert applet._power_transition_in_progress is True

        applet._power_transition_in_progress = False
        applet._state = _state(adapters=())
        applet._set_power_async(target=True)
        assert applet._power_transition_in_progress is False

        applet._state = _state(adapters=(_adapter(powered=True),))
        applet._active_adapter_path = "/org/bluez/hci0"
        monkeypatch.setattr(bluetooth_applet_mod.time, "monotonic", lambda: 10.0)
        monkeypatch.setattr(
            bluetooth_applet_mod.GLib,
            "idle_add",
            lambda func, target, ok, state: func(target, ok, state),
        )

        class _Thread:
            def __init__(self, target, daemon=True):
                self._target = target

            def start(self):
                self._target()

        applet._stop_local_discovery = lambda quiet=True: True  # type: ignore[assignment]
        applet.present = lambda: None  # type: ignore[assignment]
        applet._set_power_async(target=False)
        assert backend.power_calls

    def test_power_result_discovery_toggle_and_pref_bool(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet.present = lambda: None  # type: ignore[assignment]
        applet._local_discovery_active = True
        applet._on_power_result(False, True, _state())
        assert applet._local_discovery_active is False
        applet._on_power_result(True, False, _state())
        assert applet._action_error == "Power on failed."

        class _Widget:
            def __init__(self, active: bool):
                self._active = active

            def get_active(self):
                return self._active

        calls: list[str] = []
        applet._ensure_discovery = lambda: calls.append("ensure")  # type: ignore[assignment]
        applet._run_async = lambda action: calls.append("run")  # type: ignore[assignment]
        applet._on_continuous_discovery_toggled(_Widget(active=True))
        applet._on_continuous_discovery_toggled(_Widget(active=False))
        assert calls == ["ensure", "run"]

        applet._local_discovery_active = False
        assert applet._stop_local_discovery(quiet=True) is True
        applet._local_discovery_active = True
        applet._backend.stop_discovery = (  # type: ignore[attr-defined]
            lambda adapter_path, quiet=True: False
        )
        assert applet._stop_local_discovery(quiet=True) is False

        assert bluetooth_applet_mod._as_pref_bool(True, default=False) is True
        assert bluetooth_applet_mod._as_pref_bool("yes", default=False) is True
        assert bluetooth_applet_mod._as_pref_bool("off", default=True) is False
        assert bluetooth_applet_mod._as_pref_bool("unknown", default=True) is True
        assert bluetooth_applet_mod._as_pref_bool(0, default=True) is False


class TestBluetoothRender:
    def test_icon_renders(self):
        for size in (32, 48, 64):
            pixbuf = create_bluetooth_icon(
                size=size,
                available=True,
                powered=True,
                discovering=True,
                connected_devices=2,
            )
            assert pixbuf is not None
            assert pixbuf.get_width() == size
            assert pixbuf.get_height() == size
