"""Native Bluetooth inventory ownership, radio identity and shared controls."""

import base64
import ctypes
import json
import subprocess
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

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
        self.removals = []
        self.pairings = []
        self.accept = True
        self.discovery_starts = []
        self.discovery_stops = []
        self.closed = False

    def close(self):
        self.closed = True
        self.radios = tuple(replace(r, discovering=False) for r in self.radios)

    def start_discovery(self, target):
        self.discovery_starts.append((target.identifier, target.address))
        if self.accept:
            self.radios = tuple(
                replace(r, discovering=True) if r.identifier == target.identifier else r
                for r in self.radios
            )
        return self.accept

    def stop_discovery(self, identifier):
        self.discovery_stops.append(identifier)
        self.radios = tuple(
            replace(r, discovering=False) if r.identifier == identifier else r
            for r in self.radios
        )
        return True

    def snapshot(self):
        return self.radios

    def pair_device(self, target, device, timeout_s):
        self.pairings.append((target.identifier, device.address, timeout_s))
        if self.accept:
            self.radios = tuple(
                replace(
                    r,
                    devices=tuple(
                        replace(d, paired=True) if d.address == device.address else d
                        for d in r.devices
                    ),
                )
                if r.identifier == target.identifier
                else r
                for r in self.radios
            )
        return self.accept

    def set_power(self, target, powered):
        self.writes.append((target.identifier, powered))
        if self.accept:
            self.radios = tuple(
                replace(
                    r, state=1 if powered else 2, discovering=r.discovering and powered
                )
                if r.identifier == target.identifier
                else r
                for r in self.radios
            )
        return self.accept

    def remove_pairing(self, target, device):
        self.removals.append((target.identifier, device.address))
        if self.accept:
            self.radios = tuple(
                replace(
                    r,
                    devices=tuple(
                        replace(d, paired=False) if d.address == device.address else d
                        for d in r.devices
                    ),
                )
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
    assert not client.capabilities.connect and client.capabilities.discovery


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


def test_discovery_uses_the_selected_powered_radio_and_stops_only_its_session(backend):
    client, api = backend
    assert client.start_discovery("radio/first")
    assert api.discovery_starts == [("radio/first", 0x112233445566)]
    assert [r.discovering for r in client.get_state().adapters] == [True, False]
    assert not client.start_discovery("radio/second")
    assert not client.start_discovery("removed-radio")
    assert client.stop_discovery("radio/first")
    assert api.discovery_stops == ["radio/first"]
    assert not any(r.discovering for r in client.get_state().adapters)
    client.close()
    assert api.closed and not client.start_discovery("radio/first")


def test_closed_discovery_start_does_not_claim_a_live_session(backend):
    client, api = backend

    def start(_target):
        client.close()
        return True

    api.start_discovery = start
    assert not client.start_discovery("radio/first")


def test_discovery_request_failure_can_recover_without_claiming_success(backend):
    client, api = backend
    api.accept = False
    assert not client.start_discovery("radio/first")
    assert not any(r.discovering for r in client.get_state().adapters)
    api.accept = True
    assert client.start_discovery("radio/first")


def test_remove_pairing_targets_fresh_device_and_verifies_authentication_readback(
    backend,
):
    client, api = backend
    device = client.get_state().devices[0]
    assert client.capabilities.remove_pairing
    assert client.remove_device(device.adapter_path, device.path)
    assert api.removals == [("radio/first", 0xAABBCCDDEEFF)]
    assert not client.get_state().devices[0].paired


@pytest.mark.parametrize(
    "reason", ["wrong-adapter", "stale-device", "unpaired", "closed"]
)
def test_remove_pairing_rejects_stale_or_unowned_targets(backend, reason):
    client, api = backend
    device = client.get_state().devices[0]
    adapter, path = device.adapter_path, device.path
    if reason == "wrong-adapter":
        adapter = "radio/second"
    elif reason == "stale-device":
        path += "0"
    elif reason == "unpaired":
        api.radios = (
            replace(
                api.radios[0],
                devices=(replace(api.radios[0].devices[0], paired=False),),
            ),
        )
    else:
        client.close()
    assert not client.remove_device(adapter, path)
    assert api.removals == []


def test_removal_success_without_changed_authentication_is_not_reported_as_complete(
    backend,
):
    client, api = backend
    device = client.get_state().devices[0]
    api.remove_pairing = lambda *_args: True
    assert not client.remove_device(device.adapter_path, device.path)


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

    def fail(_radio, *, inquiry=False):
        assert not inquiry
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


def test_inquiry_uses_one_selected_handle_and_keeps_unknown_classic_devices():
    value = inventory()

    def first(search, output):
        params = search._obj
        assert params.radio == 42 and params.inquiry
        assert params.timeout == 1
        assert params.authenticated and params.connected and params.remembered
        assert params.unknown
        output._obj.address = 0xAABBCCDDEE11
        output._obj.device_class = 0x0500
        return 55

    value._first_device = first
    value._next_device = lambda *_args: False
    devices = value._devices(ctypes.c_void_p(42), inquiry=True)
    assert len(devices) == 1 and devices[0].address == 0xAABBCCDDEE11
    assert not devices[0].paired and not devices[0].connected
    assert value.closed == [("devices", 55)]


def test_inquiry_never_falls_back_to_all_radios_for_a_missing_handle():
    value = inventory()
    value._first_device = Mock(side_effect=AssertionError("Global inquiry requested"))
    with pytest.raises(ValueError, match="selected radio"):
        value._devices(ctypes.c_void_p(), inquiry=True)
    value._first_device.assert_not_called()


def test_inventory_inquires_only_on_the_requested_radio_and_closes_both_handles():
    value = inventory()

    def first(_size, output):
        output._obj.value = 42
        return 99

    def following(_cursor, output):
        if output._obj.value == 0 and not calls:
            pytest.fail("First radio was not read")
        if len(calls) == 1:
            output._obj.value = 43
            return True
        return False

    def info(handle, output):
        output._obj.address = {42: 0x112233445566, 43: 0x221133445566}[handle.value]
        return 0

    calls = []
    value._first_radio = first
    value._next_radio = following
    value._info = info
    value._devices = lambda handle, *, inquiry: (
        calls.append((handle.value, inquiry)) or ()
    )
    value.snapshot(inquiry_addresses=frozenset((0x221133445566,)))
    assert calls == [(42, False), (43, True)]
    assert value.closed == [("radio", 42), ("radio", 43), ("radios", 99)]


@pytest.fixture
def inquiry_api(monkeypatch):
    state = SimpleNamespace(
        entries=[
            {"id": "first", "address": 0x112233445566, "name": "First", "state": 1},
            {"id": "second", "address": 0x221133445566, "name": "Second", "state": 1},
        ],
        calls=[],
    )

    class Inventory:
        def snapshot(self, *, inquiry_addresses=frozenset()):
            state.calls.append(inquiry_addresses)
            return {0x112233445566: (), 0x221133445566: ()}

    monkeypatch.setattr(radio, "_request", lambda _request: {"radios": state.entries})
    monkeypatch.setattr(radio, "ClassicInventory", Inventory)
    return radio.WindowsBluetoothAPI(), state


def test_api_default_is_cached_and_owned_discovery_preserves_other_radio_sessions(
    inquiry_api,
):
    api, state = inquiry_api
    first, second = api.snapshot()
    assert state.calls == [frozenset()]
    assert not first.discovering and not second.discovering
    assert api.start_discovery(first)
    assert state.calls[-1] == frozenset((first.address,))
    assert api.start_discovery(second)
    assert all(r.discovering for r in api.snapshot())
    assert state.calls[-1] == frozenset((first.address, second.address))
    assert api.stop_discovery(first.identifier)
    assert [r.discovering for r in api.snapshot()] == [False, True]
    assert state.calls[-1] == frozenset((second.address,))
    api.close()
    before = list(state.calls)
    assert api.snapshot() == () and not api.start_discovery(second)
    assert state.calls == before


@pytest.mark.parametrize("change", ["address", "off", "removed"])
def test_stale_or_powered_off_radio_identity_is_pruned_before_another_inquiry(
    inquiry_api, change
):
    api, state = inquiry_api
    first = api.snapshot()[0]
    assert api.start_discovery(first)
    if change == "address":
        state.entries[0]["address"] = 0x998877665544
    elif change == "off":
        state.entries[0]["state"] = 2
    else:
        state.entries = state.entries[1:]
    state.calls.clear()
    assert not any(r.discovering for r in api.snapshot())
    assert all(not addresses for addresses in state.calls)


def test_api_closed_during_native_inquiry_does_not_keep_a_discovery_session(
    inquiry_api, monkeypatch
):
    api, _state = inquiry_api
    first = api.snapshot()[0]

    def snapshot(*, inquiry_addresses):
        assert inquiry_addresses == frozenset((first.address,))
        api.close()
        return {first.address: ()}

    monkeypatch.setattr(
        radio, "ClassicInventory", lambda: SimpleNamespace(snapshot=snapshot)
    )
    assert not api.start_discovery(first)
    assert api.snapshot() == ()


def test_api_stop_during_native_inquiry_cancels_the_pending_start(
    inquiry_api, monkeypatch
):
    api, _state = inquiry_api
    first = api.snapshot()[0]

    def snapshot(*, inquiry_addresses=frozenset()):
        if inquiry_addresses:
            api.stop_discovery(first.identifier)
        return {first.address: ()}

    monkeypatch.setattr(
        radio, "ClassicInventory", lambda: SimpleNamespace(snapshot=snapshot)
    )
    assert not api.start_discovery(first)
    assert not any(item.discovering for item in api.snapshot())


def test_discovery_cleanup_does_not_wait_for_the_poll_lock(backend):
    import threading

    client, api = backend
    done = threading.Event()
    results = []

    def cleanup():
        results.append(client.stop_discovery("radio/first"))
        client.close()
        done.set()

    with client._lock:
        worker = threading.Thread(target=cleanup, daemon=True)
        worker.start()
        assert done.wait(5), "Discovery cleanup waited for a running poll"
    worker.join(5)
    assert results == [True] and api.discovery_stops == ["radio/first"] and api.closed


@pytest.mark.parametrize("address", [0, -1, 1 << 48, True])
def test_invalid_inquiry_addresses_never_reach_radio_enumeration(address):
    value = inventory()
    value._first_radio = Mock(side_effect=AssertionError("Invalid inquiry started"))
    with pytest.raises(ValueError, match="valid radio addresses"):
        value.snapshot(inquiry_addresses=frozenset((address,)))
    value._first_radio.assert_not_called()


@pytest.mark.skipif(
    sys.platform != "win32", reason="Native Windows classic inquiry ABI"
)
def test_native_missing_radio_cannot_start_a_global_or_owned_inquiry():
    if sys.getwindowsversion().product_type != 1:
        pytest.skip("Classic inquiry acceptance targets Windows clients")
    if radio.ClassicInventory().snapshot():
        pytest.skip("Missing-radio acceptance needs an adapter-free VM")
    api = radio.WindowsBluetoothAPI()
    try:
        assert not api.start_discovery(
            radio.Radio("docking-missing-radio-fixture", 0x112233445566, "Fixture", 1)
        )
    finally:
        api.close()


def test_stopping_during_poll_discards_unknown_results_and_the_discovering_flag(
    inquiry_api, monkeypatch
):
    api, _state = inquiry_api
    first = api.snapshot()[0]
    assert api.start_discovery(first)
    nearby = radio.ClassicDevice(0xAABBCCDDEE22, "Nearby fixture", 0x0500, False, False)

    def snapshot(*, inquiry_addresses):
        assert inquiry_addresses == frozenset((first.address,))
        api.stop_discovery(first.identifier)
        return {first.address: (nearby,)}

    monkeypatch.setattr(
        radio, "ClassicInventory", lambda: SimpleNamespace(snapshot=snapshot)
    )
    refreshed = api.snapshot()[0]
    assert not refreshed.discovering and refreshed.devices == ()


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


def test_shared_menu_exposes_pairing_removal_without_unsupported_connection_actions(
    backend,
    monkeypatch,
):
    from docking.applets.bluetooth import applet as ui
    from docking.core.config import Config

    client, api = backend
    monkeypatch.setattr(ui, "create_backend", lambda: client)
    config = Config()
    config.applet_prefs["bluetooth"] = {"continuous_discovery": False}
    applet = ui.BluetoothApplet(48, config)
    applet._on_poll_result(client.get_state())
    rows = applet.get_menu_items()
    labels = [row.get_label() for row in rows]
    assert "Devices..." in labels and "Turn Bluetooth Off" in labels
    assert "Continuous Discovery" in labels
    assert "Recent Connections" not in labels and "Disconnect Headphones" not in labels
    device = next(row for row in rows if "Headphones" in (row.get_label() or ""))
    submenu = device.get_submenu()
    assert submenu is not None and device.get_sensitive()
    actions = submenu.get_children()
    assert [row.get_label() for row in actions] == ["Remove Pairing"]
    applet._run_async = lambda task: task()
    for callback, args in actions[0]._signals["activate"]:
        callback(actions[0], *args)
    assert api.removals == [("radio/first", 0xAABBCCDDEEFF)]
    assert not client.get_state().devices[0].paired
    assert not applet._local_discovery_paths
    applet.stop()


def test_closing_during_pairing_removal_does_not_read_or_publish_late_state(backend):
    client, api = backend
    device = client.get_state().devices[0]

    def remove(*_args):
        client.close()
        api.snapshot = lambda: pytest.fail("Closed client read late device state")
        return True

    api.remove_pairing = remove
    assert not client.remove_device(device.adapter_path, device.path)


def test_native_removal_revalidates_radio_and_paired_device_before_exact_address_call():
    value = inventory()
    device = radio.ClassicDevice(0xAABBCCDDEEFF, "Fixture", 0, True, False)
    value.snapshot = lambda: {0x112233445566: (device,)}
    calls = []

    def remove(address):
        calls.append(address._obj.value)
        return 0

    value._remove = remove
    assert value.remove_pairing(0x112233445566, device.address)
    assert calls == [device.address]
    assert not value.remove_pairing(0x112233445567, device.address)
    assert not value.remove_pairing(0x112233445566, device.address + 1)
    assert calls == [device.address]
    value.snapshot = lambda: {0x112233445566: (replace(device, paired=False),)}
    assert not value.remove_pairing(0x112233445566, device.address)
    assert calls == [device.address]


@pytest.mark.parametrize("address", [0, -1, 1 << 48, True])
def test_native_removal_rejects_invalid_addresses_without_inventory_or_write(address):
    value = inventory()
    value.snapshot = Mock(
        side_effect=AssertionError("Invalid address queried inventory")
    )
    value._remove = Mock(side_effect=AssertionError("Invalid address reached Windows"))
    assert not value.remove_pairing(address, 0xAABBCCDDEEFF)
    assert not value.remove_pairing(0x112233445566, address)


def test_native_removal_preserves_windows_failure_instead_of_reporting_success():
    value = inventory()
    device = radio.ClassicDevice(0xAABBCCDDEEFF, "Fixture", 0, True, False)
    value.snapshot = lambda: {0x112233445566: (device,)}
    value._remove = lambda _address: 1168
    with pytest.raises(OSError) as error:
        value.remove_pairing(0x112233445566, device.address)
    assert error.value.errno == 1168


@pytest.mark.skipif(
    sys.platform != "win32", reason="Native Windows client Bluetooth ABI"
)
def test_native_removal_binding_rejects_an_invalid_device_without_changing_pairings():
    if sys.getwindowsversion().product_type != 1:
        pytest.skip("Microsoft supports BluetoothRemoveDevice on Windows clients")
    value = radio.ClassicInventory()
    assert value._remove.restype is ctypes.c_uint32
    assert value._remove.argtypes == (ctypes.POINTER(ctypes.c_uint64),)
    assert value._remove(ctypes.byref(ctypes.c_uint64(0))) in (87, 1168)


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


@pytest.mark.parametrize(
    "mode", ["success", "denied", "no-readback", "radio-replaced", "closed"]
)
def test_pairing_targets_fresh_device_and_requires_matching_readback(backend, mode):
    client, api = backend
    original = api.radios[0]
    device = replace(original.devices[0], paired=False)
    api.radios = (replace(original, devices=(device,)),)
    pair = api.pair_device

    def perform(radio, target, timeout):
        if mode == "denied":
            api.accept = False
        result = pair(radio, target, timeout)
        if mode == "no-readback":
            api.radios = (replace(original, devices=(device,)),)
        if mode == "radio-replaced":
            api.radios = (replace(api.radios[0], address=0x221133445566),)
        if mode == "closed":
            client.close()
        return result

    api.pair_device = perform
    assert client.pair_device("radio/first/aabbccddeeff", "AA:BB:CC:DD:EE:FF", 30) is (
        mode == "success"
    )
    assert api.pairings == [("radio/first", device.address, 30)]


@pytest.mark.parametrize(
    "mode", ["missing", "off", "policy", "address", "timeout", "closed", "paired"]
)
def test_invalid_or_already_paired_targets_do_not_launch_a_prompt(backend, mode):
    client, api = backend
    first = api.radios[0]
    device = replace(first.devices[0], paired=mode == "paired")
    api.radios = (
        replace(
            first,
            state=2 if mode == "off" else 3 if mode == "policy" else 1,
            devices=(device,),
        ),
    )
    if mode == "closed":
        client.close()
    assert client.pair_device(
        "missing" if mode == "missing" else "radio/first/aabbccddeeff",
        "00:11:22:33:44:55" if mode == "address" else "",
        True if mode == "timeout" else 20,
    ) is (mode == "paired")
    assert api.pairings == []


def selected_inventory():
    value = inventory()

    def first(_size, output):
        output._obj.value = 42
        return 99

    def info(_handle, output):
        output._obj.address = 0x112233445566
        return 0

    value._first_radio = first
    value._info = info
    value._next_radio = lambda *_args: False
    return value


def test_selected_radio_handle_is_closed_when_the_operation_raises():
    value = selected_inventory()
    with pytest.raises(RuntimeError), value.radio_handle(0x112233445566) as handle:
        assert handle.value == 42
        assert value.closed == []
        raise RuntimeError("Operation failed")
    assert value.closed == [("radio", 42), ("radios", 99)]


def test_missing_selected_radio_closes_enumeration_without_global_fallback():
    value = selected_inventory()
    with value.radio_handle(0x221133445566) as handle:
        assert handle is None
    assert value.closed == [("radio", 42), ("radios", 99)]


def test_api_close_cancels_pairing_without_waiting_for_its_worker(monkeypatch):
    import threading
    import time

    entered, released = threading.Event(), threading.Event()
    operation = Mock()

    def run(*_args):
        entered.set()
        assert released.wait(5)
        return True

    operation.run = run
    monkeypatch.setattr(radio, "PairingOperation", lambda: operation)
    api = radio.WindowsBluetoothAPI()
    first = RadioAPI().radios[0]
    results = []
    worker = threading.Thread(
        target=lambda: results.append(api.pair_device(first, first.devices[0], 20))
    )
    worker.start()
    try:
        assert entered.wait(5)
        assert not api.pair_device(first, first.devices[0], 20)
        before = time.monotonic()
        api.close()
        assert time.monotonic() - before < 0.25
        operation.cancel.assert_called_once_with()
    finally:
        released.set()
        worker.join(5)
    assert not worker.is_alive() and results == [False]
    assert not api.pair_device(first, first.devices[0], 20)


def test_api_pairing_failure_releases_the_operation_slot(monkeypatch):
    operation = Mock()
    operation.run.side_effect = [OSError("Prompt unavailable"), True]
    monkeypatch.setattr(radio, "PairingOperation", lambda: operation)
    api = radio.WindowsBluetoothAPI()
    first = RadioAPI().radios[0]
    with pytest.raises(OSError):
        api.pair_device(first, first.devices[0], 20)
    assert api.pair_device(first, first.devices[0], 20)
