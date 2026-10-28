"""Native Bluetooth inventory ownership, radio identity and shared controls."""

import base64
import ctypes
import json
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from docking.applets.bluetooth import windows as mod
from docking.applets.bluetooth import windows_radio as radio
from docking.applets.bluetooth.backend import BluetoothAction, create_backend
from docking.platform import environment


class RadioAPI:
    def __init__(self):
        self.radios = (
            radio.Radio(
                "radio/first",
                0x112233445566,
                "First",
                1,
                (
                    radio.ClassicDevice(
                        0xAABBCCDDEEFF, "Headphones", 0x0404, True, True
                    ),
                ),
            ),
            radio.Radio("radio/second", 0x221133445566, "Second", 2),
        )
        self.writes = []
        self.accept = True

    def snapshot(self):
        return self.radios

    def set_power(self, target, powered):
        self.writes.append((target.identifier, powered))
        if self.accept:
            self.radios = tuple(
                replace(r, state=1 if powered else 2)
                if r.identifier == target.identifier
                else r
                for r in self.radios
            )
        return self.accept


@pytest.fixture
def backend():
    api = RadioAPI()
    return mod.WindowsBluetoothBackend(api=api), api


def test_factory_never_loads_unix_bluetooth_on_windows(monkeypatch):
    monkeypatch.setattr(environment, "is_windows", lambda: True)
    monkeypatch.setitem(sys.modules, "docking.applets.bluetooth.linux", None)
    monkeypatch.setitem(sys.modules, "docking.applets.bluetooth.bsd", None)
    assert isinstance(create_backend(), mod.WindowsBluetoothBackend)


def test_inventory_preserves_adapter_identity_and_unknown_metadata(backend):
    client, _ = backend
    state = client.get_state("radio/second")
    assert state.active_adapter_path == "radio/second"
    assert [(a.path, a.powered) for a in state.adapters] == [
        ("radio/first", True),
        ("radio/second", False),
    ]
    device = state.devices[0]
    assert device.adapter_path == "radio/first"
    assert device.address == "aa:bb:cc:dd:ee:ff"
    assert device.connected and device.paired
    assert not device.trusted
    assert device.battery_percent is None and device.rssi is None
    assert not client.capabilities.connect and not client.capabilities.discovery


@pytest.mark.parametrize("state", [0, 3])
def test_unknown_or_policy_disabled_radio_has_no_power_control(backend, state):
    client, api = backend
    api.radios = (replace(api.radios[0], state=state),)
    adapter = client.get_state().adapters[0]
    assert adapter.powered is (None if state == 0 else False)
    assert not adapter.power_controllable
    assert not client.set_adapter_power(adapter.path, True)
    assert api.writes == []


def test_power_targets_exact_fresh_adapter_and_verifies_readback(backend):
    client, api = backend
    assert client.set_adapter_power("radio/second", True)
    assert api.writes == [("radio/second", True)]
    assert client.get_state().adapters[0].powered
    assert client.get_state().adapters[1].powered
    assert client.set_adapter_power("radio/second", True)  # already applied
    assert len(api.writes) == 1
    api.accept = False
    assert not client.set_adapter_power("radio/first", False)
    assert client.get_state().adapters[0].powered


def test_successful_request_without_completed_state_change_is_failure(backend):
    client, api = backend
    api.set_power = lambda *args: True
    assert not client.set_adapter_power("radio/first", False)


def test_removed_adapter_and_closed_client_never_change_another_radio(backend):
    client, api = backend
    api.radios = (api.radios[1],)
    assert not client.set_adapter_power("radio/first", False)
    client.close()
    assert not client.get_state().available
    assert not client.set_adapter_power("radio/second", True)
    assert api.writes == [] and client.desktop_actions() == ()


@pytest.mark.parametrize(
    "error",
    [
        OSError("unavailable"),
        ValueError("bad"),
        subprocess.TimeoutExpired("provider", 8),
    ],
)
def test_provider_failure_never_becomes_success(backend, error):
    client, api = backend

    def fail():
        raise error

    api.snapshot = fail
    assert not client.get_state().available
    assert not client.set_adapter_power("radio/first", False)


def test_windows_settings_are_available_without_hardware_and_no_unix_command(
    backend,
    monkeypatch,
    tmp_path,
):
    client, api = backend
    api.radios = ()
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    opened = []
    monkeypatch.setattr(mod.os, "startfile", opened.append, raising=False)
    assert client.desktop_actions() == (BluetoothAction.DEVICES,)
    assert client.open_desktop_action(BluetoothAction.DEVICES)
    assert opened == ["ms-settings:bluetooth"]
    assert not client.open_desktop_action(BluetoothAction.SEND_FILES)
    client.close()
    assert not client.open_desktop_action(BluetoothAction.DEVICES)


def test_send_files_uses_system_wizard_and_send_mode_without_hardware(
    backend, monkeypatch, tmp_path
):
    client, api = backend
    api.radios = ()
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    executable = tmp_path / "System32/fsquirt.exe"
    executable.parent.mkdir()
    executable.touch()
    opened = []
    monkeypatch.setattr(
        mod.os,
        "startfile",
        lambda target, **kwargs: opened.append((target, kwargs)),
        raising=False,
    )
    assert client.desktop_actions() == (
        BluetoothAction.SEND_FILES,
        BluetoothAction.DEVICES,
    )
    assert client.open_desktop_action(BluetoothAction.SEND_FILES)
    assert opened == [(str(executable), {"arguments": "-send"})]
    assert not client.open_desktop_action(BluetoothAction.LOCAL_SERVICES)
    executable.unlink()
    assert not client.open_desktop_action(BluetoothAction.SEND_FILES)
    assert len(opened) == 1
    executable.touch()
    client.close()
    assert not client.open_desktop_action(BluetoothAction.SEND_FILES)
    assert client.desktop_actions() == () and len(opened) == 1


def test_send_files_reports_denied_launch(backend, monkeypatch, tmp_path):
    client, _ = backend
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
    executable = tmp_path / "System32/fsquirt.exe"
    executable.parent.mkdir()
    executable.touch()

    def denied(*args, **kwargs):
        raise PermissionError("File transfer blocked")

    monkeypatch.setattr(mod.os, "startfile", denied, raising=False)
    assert not client.open_desktop_action(BluetoothAction.SEND_FILES)


def test_bluetooth_structures_match_windows_abi():
    assert ctypes.sizeof(radio._RadioInfo) == 520
    assert ctypes.sizeof(radio._DeviceInfo) == 560
    assert radio._DeviceInfo.name.offset == 64
    assert ctypes.sizeof(radio._Search) == (
        40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 32
    )


def inventory():
    value = radio.ClassicInventory.__new__(radio.ClassicInventory)
    value.closed = []
    value._close_handle = lambda p: value.closed.append(("radio", p.value))
    value._close_radios = lambda p: value.closed.append(("radios", p))
    value._close_devices = lambda p: value.closed.append(("devices", p))
    value._end = lambda: None
    return value


def test_radio_and_enumeration_handles_close_when_device_read_fails():
    value = inventory()

    def first(_size, output):
        output._obj.value = 42
        return 99

    value._first_radio = first
    value._info = lambda *args: 0

    def fail(_radio):
        raise OSError("device disappeared")

    value._devices = fail
    with pytest.raises(OSError):
        value.snapshot()
    assert value.closed == [("radio", 42), ("radios", 99)]


def test_partial_radio_handle_closes_even_when_enumeration_fails():
    value = inventory()

    def first(_size, output):
        output._obj.value = 42
        return 0

    value._first_radio = first
    assert value.snapshot() == {}
    assert value.closed == [("radio", 42)]


def test_device_enumeration_is_cached_scoped_and_closes_after_failure():
    value = inventory()

    def first(search, output):
        assert search._obj.radio == 42 and not search._obj.inquiry
        assert not search._obj.unknown and not search._obj.remembered
        assert search._obj.authenticated and search._obj.connected
        output._obj.address = 0xAABBCCDDEEFF
        output._obj.authenticated = 1
        return 55

    value._first_device = first

    def fail(*args):
        raise OSError("hot unplug")

    value._next_device = fail
    with pytest.raises(OSError):
        value._devices(ctypes.c_void_p(42))
    assert value.closed == [("devices", 55)]


@pytest.mark.parametrize(
    "field,value",
    [
        ("address", True),
        ("address", 1 << 48),
        ("state", True),
        ("state", 10),
        ("id", "bad\0id"),
        ("name", None),
    ],
)
def test_invalid_winrt_properties_are_not_projected(monkeypatch, field, value):
    entry = {"id": "adapter", "name": "Name", "address": 1, "state": 1}
    entry[field] = value
    monkeypatch.setattr(radio, "_request", lambda _: {"radios": [entry]})
    monkeypatch.setattr(
        radio, "ClassicInventory", lambda: SimpleNamespace(snapshot=dict)
    )
    with pytest.raises(ValueError):
        radio.WindowsBluetoothAPI().snapshot()


def test_provider_request_is_encoded_bounded_and_never_uses_a_shell(monkeypatch):
    request = {"action": "power", "id": 'ID";$(oops)', "powered": False}

    def run(argv, **kwargs):
        assert argv[1:4] == ["-NoProfile", "-NonInteractive", "-EncodedCommand"]
        script = base64.b64decode(argv[4]).decode("utf-16-le")
        data = base64.b64encode(json.dumps(request).encode()).decode()
        assert data in script and request["id"] not in script
        assert kwargs["timeout"] == 8 and "shell" not in kwargs
        return SimpleNamespace(returncode=0, stdout='{"changed":false,"radios":[]}')

    monkeypatch.setattr(radio.subprocess, "run", run)
    assert radio._request(request)["changed"] is False


def test_shared_menu_exposes_only_supported_actions_and_read_only_devices(
    backend,
    monkeypatch,
):
    from docking.applets.bluetooth import applet as ui
    from docking.core.config import Config

    client, _ = backend
    monkeypatch.setattr(ui, "create_backend", lambda: client)
    applet = ui.BluetoothApplet(48, Config())
    applet._on_poll_result(client.get_state())
    rows = applet.get_menu_items()
    labels = [row.get_label() for row in rows]
    assert "Devices..." in labels and "Turn Bluetooth Off" in labels
    assert "Continuous Discovery" not in labels
    assert "Recent Connections" not in labels and "Disconnect Headphones" not in labels
    device = next(row for row in rows if "Headphones" in (row.get_label() or ""))
    assert device.get_submenu() is None and not device.get_sensitive()
    assert not applet._local_discovery_paths
    applet.stop()


def test_shared_menu_keeps_settings_when_no_adapter_exists(backend, monkeypatch):
    from docking.applets.bluetooth import applet as ui
    from docking.core.config import Config

    client, api = backend
    api.radios = ()
    monkeypatch.setattr(ui, "create_backend", lambda: client)
    applet = ui.BluetoothApplet(48, Config())
    labels = [row.get_label() for row in applet.get_menu_items()]
    assert "Bluetooth unavailable" in labels and "Devices..." in labels
    applet.stop()


def test_old_power_menu_cannot_toggle_newly_selected_adapter(backend, monkeypatch):
    from docking.applets.bluetooth import applet as ui
    from docking.core.config import Config

    client, api = backend
    monkeypatch.setattr(ui, "create_backend", lambda: client)
    applet = ui.BluetoothApplet(48, Config())
    applet._on_poll_result(client.get_state())
    power = next(
        row
        for row in applet.get_menu_items()
        if row.get_label() == "Turn Bluetooth Off"
    )
    applet._active_adapter_path = "radio/second"
    for callback, args in power._signals["activate"]:
        callback(power, *args)
    assert not applet._power_transition_in_progress and api.writes == []
    applet.stop()


@pytest.mark.skipif(sys.platform != "win32", reason="native Bluetooth service")
def test_native_read_only_bluetooth_inventory():
    state = mod.WindowsBluetoothBackend().get_state()
    assert isinstance(state.available, bool)
    assert not state.available or state.adapters
