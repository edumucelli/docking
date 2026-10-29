"""Integrated Windows endpoint ownership, request cancellation and live metadata."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.bluetooth import windows_le as mod
from docking.applets.bluetooth.windows_advertisement import Advertisement
from docking.platform.backends.windows.winrt_collections import parameterized_iid


def device(**values):
    return mod.LowEnergyDevice(
        **{
            "identifier": "explicit-LE-fixture",
            "address": 0xAABBCCDDEEFF,
            "name": "Fixture keyboard",
            "paired": True,
            "connected": False,
            "can_pair": True,
            "address_type": 0,
            "appearance": 961,
            **values,
        }
    )


class Runtime:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass


@pytest.fixture
def broker(monkeypatch):
    entered, released, closed = threading.Event(), threading.Event(), threading.Event()
    state = SimpleNamespace(
        calls=[], block=False, closed=closed, records=(device(),), snapshot_error=None
    )

    class API:
        def __init__(self, _runtime, cancelled):
            self.cancelled = cancelled
            self._discovering = False

        def default_adapter(self):
            return "adapter", 0x112233445566

        def snapshot(self):
            state.calls.append(("snapshot", threading.get_ident()))
            if state.snapshot_error is not None:
                raise state.snapshot_error
            if state.block:
                entered.set()
                while not released.wait(0.01):
                    if self.cancelled.is_set():
                        raise OSError("Cancelled")
            return mod.LowEnergySnapshot("adapter", 0x112233445566, state.records)

        def watch(self, enabled):
            self._discovering = enabled
            state.calls.append(("watch", enabled, threading.get_ident()))

        def release_connections(self):
            state.calls.append(("release", threading.get_ident()))

        def connect(self, target):
            state.calls.append(("connect", target.identifier, threading.get_ident()))
            state.records = (replace(target, connected=True, owned_connection=True),)
            return True

        def disconnect(self, target):
            state.calls.append(("disconnect", target.identifier, threading.get_ident()))
            state.records = (replace(target, owned_connection=False),)
            return True

        def unpair(self, target):
            state.calls.append(("unpair", target.identifier, threading.get_ident()))
            state.records = (replace(target, paired=False),)
            return True

        def close(self):
            state.calls.append(("close", threading.get_ident()))
            closed.set()

    monkeypatch.setattr(mod, "WindowsRuntime", Runtime)
    monkeypatch.setattr(mod, "EndpointAPI", API)
    client = mod.LowEnergyClient()
    try:
        yield client, state, entered, released
    finally:
        client.close()
        released.set()
        if client._thread is not None:
            assert closed.wait(3)
            client._thread.join(3)
            assert not client._thread.is_alive()


def test_all_endpoint_operations_stay_on_one_owned_worker(broker):
    client, state, _entered, _released = broker
    target = client.snapshot().devices[0]
    assert client.connect(target, ("adapter", 0x112233445566))
    assert client.snapshot().devices[0].owned_connection
    assert client.disconnect(target, ("adapter", 0x112233445566))
    assert not client.snapshot().devices[0].owned_connection
    assert client.unpair(target, ("adapter", 0x112233445566))
    assert not client.snapshot().devices[0].paired
    threads = {call[-1] for call in state.calls}
    assert len(threads) == 1 and threading.get_ident() not in threads


def test_closed_or_replaced_adapter_never_receives_a_device_operation(broker):
    client, state, _entered, _released = broker
    target = device()
    for adapter in (("replaced", 0x112233445566), ("adapter", 0x221133445566)):
        assert not client.connect(target, adapter)
        assert not client.disconnect(target, adapter)
        assert not client.unpair(target, adapter)
    client.close()
    assert not client.connect(target, ("adapter", 0x112233445566))
    assert not state.calls


def test_close_cancels_inflight_native_read_without_blocking_gtk(broker):
    client, state, entered, _released = broker
    state.block = True
    results = []
    caller = threading.Thread(target=lambda: results.append(client.snapshot()))
    caller.start()
    assert entered.wait(3)
    before = time.monotonic()
    client.close()
    assert time.monotonic() - before < 0.25
    caller.join(3)
    assert not caller.is_alive()
    assert results == [mod.LowEnergySnapshot("", 0, ())]
    assert state.closed.wait(3)


def test_discovery_stop_is_nonblocking_and_prevents_a_cancelled_start(broker):
    client, state, _entered, _released = broker
    cancelled = threading.Event()
    cancelled.set()
    assert not client.discover(cancelled, ("adapter", 0x112233445566))
    assert state.calls == []
    assert client.discover(threading.Event(), ("adapter", 0x112233445566))
    before = time.monotonic()
    client.stop_discovery()
    assert time.monotonic() - before < 0.25
    client.snapshot()
    assert any(call[:2] == ("watch", False) for call in state.calls)


def test_provider_errors_are_not_converted_to_an_empty_success(broker):
    client, state, _entered, _released = broker
    state.snapshot_error = PermissionError("Denied")
    with pytest.raises(PermissionError):
        client.snapshot()
    state.snapshot_error = None
    assert client.snapshot().devices


def test_winrt_string_iterable_identity_matches_the_sdk():
    assert (
        parameterized_iid("pinterface({faa585ea-6214-4217-afda-7f46de5869b3};string)")
        == "e2fcc7c1-3bfc-5a0b-b2b0-72e769d1cb7e"
    )


@pytest.mark.parametrize(
    "raw,expected",
    [(0, 0), (100, 100), (True, None), (-1, None), (101, None), (None, None)],
)
def test_optional_battery_fields_keep_zero_and_reject_unknown_values(raw, expected):
    assert mod._integer(raw, 0, 100) == expected


def test_advertisements_preserve_address_type_and_expire_without_becoming_bonds():
    api = mod.EndpointAPI.__new__(mod.EndpointAPI)
    api._adapter = "adapter", 0x112233445566
    api._discovering = True
    now = time.monotonic()
    api._advertisements = {
        (0xAABBCCDDEEFF, 0): (
            now,
            Advertisement(0xAABBCCDDEEFF, 0, "Public", -49),
        ),
        (0xAABBCCDDEEFF, 1): (
            now,
            Advertisement(0xAABBCCDDEEFF, 1, "Random beacon", -60),
        ),
        (0x112233445577, 0): (
            now - 46,
            Advertisement(0x112233445577, 0, "Expired", -80),
        ),
    }
    endpoint = device(name="", rssi=None)
    classic = device(identifier="classic", low_energy=False)
    merged = api._merge_advertisements((endpoint, classic))
    assert len(merged) == 3
    assert merged[0].identifier == endpoint.identifier
    assert merged[0].name == "Public" and merged[0].rssi == -49
    assert merged[1] == classic
    beacon = merged[2]
    assert beacon.address == endpoint.address and beacon.address_type == 1
    assert not beacon.can_pair and not beacon.paired and not beacon.connected
    assert beacon.identifier != endpoint.identifier
    api._discovering = False
    assert api._merge_advertisements((endpoint,)) == (endpoint,)


@pytest.mark.parametrize("change", [{"identifier": "replacement"}, {"address_type": 1}])
def test_lookup_rejects_a_replaced_endpoint_even_at_the_same_address(
    monkeypatch, change
):
    api = mod.EndpointAPI.__new__(mod.EndpointAPI)
    api.runtime = SimpleNamespace(string=Mock(return_value=ExitStack()))
    api._statics = ctypes.c_void_p(1)
    api._async = Mock(return_value=ctypes.c_void_p(2))
    api.record = Mock(return_value=device(**change))
    properties = SimpleNamespace(pointer=ctypes.c_void_p(3), close=Mock())
    monkeypatch.setattr(mod, "StringIterable", Mock(return_value=properties))
    with ExitStack() as scope, pytest.raises(OSError, match="identity changed"):
        api.lookup(scope, device())
    properties.close.assert_called_once()


@pytest.mark.parametrize(
    "status,state,expected", [(0, 1, True), (2, 1, False), (0, 0, False)]
)
def test_audio_requires_both_open_result_and_live_state_and_closes_owned_objects(
    monkeypatch, status, state, expected
):
    target = device(low_energy=False, audio_endpoint="explicit-audio-endpoint")
    api = mod.EndpointAPI.__new__(mod.EndpointAPI)
    api.cancelled = threading.Event()
    api._audio_connections = {}
    api.lookup = Mock(return_value=(ctypes.c_void_p(1), target))
    api.runtime = SimpleNamespace(
        factory=Mock(return_value=ctypes.c_void_p(2)),
        string=Mock(side_effect=lambda _text: ExitStack()),
        wait=Mock(),
    )
    api._close = Mock()
    api._async = Mock(return_value=ctypes.c_void_p(5))
    pointers = iter([ctypes.c_void_p(3), ctypes.c_void_p(4)])
    monkeypatch.setattr(mod, "object_result", lambda *_args: next(pointers))
    monkeypatch.setattr(mod, "method", lambda *_args: lambda *_args2: 0)
    monkeypatch.setattr(
        mod,
        "value",
        lambda _pointer, index, _kind: ctypes.c_int32(status if index == 6 else state),
    )
    assert api.connect(target) is expected
    if expected:
        assert target.identifier in api._audio_connections
        api._close.assert_not_called()
        assert api.disconnect(target)
    api._close.assert_called_once()
    assert api._close.call_args.args[0].value == 3


@pytest.mark.parametrize(
    "can_maintain,active,remote_matches",
    [
        (True, True, True),
        (False, False, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_gatt_session_ownership_requires_active_state_and_cleanup(
    monkeypatch, can_maintain, active, remote_matches
):
    api = mod.EndpointAPI.__new__(mod.EndpointAPI)
    api.cancelled = threading.Event()
    api._connections = {}
    api._audio_connections = {}
    api.lookup = Mock(return_value=(ctypes.c_void_p(1), device()))
    api.runtime = SimpleNamespace(
        factory=Mock(return_value=ctypes.c_void_p(2)),
        string=Mock(side_effect=lambda _text: ExitStack()),
        text=Mock(return_value=device().identifier if remote_matches else "replaced"),
    )
    api._async = Mock(side_effect=[ctypes.c_void_p(3), ctypes.c_void_p(5)])
    api._close = Mock()
    api._release_session = Mock()
    monkeypatch.setattr(mod, "object_result", lambda *_args: ctypes.c_void_p(4))
    monkeypatch.setattr(mod, "method", lambda *_args: lambda *_args2: 0)
    monkeypatch.setattr(
        mod,
        "value",
        lambda _pointer, index, _kind: (
            ctypes.c_int32(int(can_maintain) if index == 7 else int(active))
            if index != 10
            else ctypes.c_uint64(device().address)
        ),
    )
    if not active:
        api.cancelled.set()
    assert api.connect(device()) is (can_maintain and active and remote_matches)
    if can_maintain and active and remote_matches:
        api._release_session.assert_not_called()
        assert device().identifier in api._connections
        api.release_connections()
    if remote_matches:
        api._release_session.assert_called_once()
        assert api._release_session.call_args.args[0].value == 5
    else:
        api._release_session.assert_not_called()
    api._close.assert_called_once()
    assert api._close.call_args.args[0].value == 3


def test_unpairing_requires_native_success_and_a_fresh_unpaired_readback(monkeypatch):
    target = device()
    api = mod.EndpointAPI.__new__(mod.EndpointAPI)
    api.disconnect = Mock(return_value=False)
    api.lookup = Mock(
        side_effect=[
            (ctypes.c_void_p(1), target),
            (ctypes.c_void_p(1), replace(target, paired=False)),
        ]
    )
    api._async = Mock(return_value=ctypes.c_void_p(2))
    monkeypatch.setattr(mod, "query", lambda *_args: ctypes.c_void_p(3))
    monkeypatch.setattr(mod, "object_result", lambda *_args: ctypes.c_void_p(4))
    monkeypatch.setattr(mod, "value", lambda *_args: ctypes.c_int32(0))
    assert api.unpair(target)
    assert api.lookup.call_count == 2
    api.lookup.side_effect = [
        (ctypes.c_void_p(1), target),
        (ctypes.c_void_p(1), target),
    ]
    assert not api.unpair(target)


@pytest.mark.skipif(sys.platform != "win32", reason="Requires native Windows")
def test_native_endpoint_factories_watchers_and_cleanup():
    errors = []

    def check_native():
        try:
            _native_endpoints()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=check_native, daemon=True)
    worker.start()
    worker.join(30)
    assert not worker.is_alive(), "Native endpoint validation did not finish"
    if errors:
        raise errors[0]


def _native_endpoints():
    from docking.platform.backends.windows.com_callback import ComObject
    from docking.platform.backends.windows.winrt import WindowsRuntime

    baseline = set(ComObject._live)
    with WindowsRuntime() as runtime:
        api = mod.EndpointAPI(runtime, threading.Event())
        try:
            snapshot = api.snapshot()
            assert isinstance(snapshot, mod.LowEnergySnapshot)
            api.watch(False)
            time.sleep(0.1)
            assert isinstance(api.devices(), tuple)
            api._read_batteries()
            api._read_audio_endpoints()
            # Activate and subscribe to the actual advertisement API without
            # starting an RF scan in this adapter-free acceptance environment.
            from docking.applets.bluetooth.windows_advertisement import (
                RECEIVED_IID,
                WATCHER_IID,
            )

            # PnP containers produce actual Added callbacks without requiring
            # a Bluetooth radio or altering any hardware.
            from docking.platform.backends.windows.com import (
                HRESULT,
                method,
                object_result,
            )
            from docking.platform.backends.windows.winrt import check
            from docking.platform.backends.windows.winrt_collections import (
                StringIterable,
                event_iid,
            )

            received = threading.Event()
            with ExitStack() as scope:
                for name, iid in (
                    ("Windows.Devices.Bluetooth.BluetoothLEDevice", mod._LE_FACTORY),
                    ("Windows.Devices.Bluetooth.BluetoothDeviceId", mod._DEVICE_ID),
                    (
                        "Windows.Devices.Bluetooth.GenericAttributeProfile.GattSession",
                        mod._GATT,
                    ),
                ):
                    assert runtime.factory(scope, name, iid).value
                factory = runtime.factory(
                    scope,
                    "Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher",
                    mod._ACTIVATION,
                )
                advertisement = mod.query(
                    scope,
                    object_result(scope, factory, 6),
                    mod.GUID.parse(WATCHER_IID),
                )
                assert mod.value(advertisement, 10, ctypes.c_int32).value == 0
                api._subscribe(
                    scope,
                    advertisement,
                    19,
                    event_iid(
                        "Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher",
                        WATCHER_IID,
                        "Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementReceivedEventArgs",
                        RECEIVED_IID,
                    ),
                    lambda _sender, _args: None,
                )
            with ExitStack() as scope, runtime.string("") as selector:
                properties = StringIterable(runtime, ("System.Devices.ContainerId",))
                try:
                    watcher = object_result(
                        scope,
                        api._statics,
                        9,
                        ctypes.c_void_p,
                        selector,
                        ctypes.c_void_p,
                        properties.pointer,
                        ctypes.c_int32,
                        2,
                    )
                finally:
                    properties.close()
                api._subscribe(
                    scope,
                    watcher,
                    6,
                    event_iid(
                        "Windows.Devices.Enumeration.DeviceWatcher",
                        mod._WATCHER,
                        "Windows.Devices.Enumeration.DeviceInformation",
                        mod._INFORMATION,
                    ),
                    lambda _sender, _args: received.set(),
                )
                check(method(watcher, 17, HRESULT)(watcher))
                try:
                    assert received.wait(5), (
                        "Native device Added callback was not delivered"
                    )
                finally:
                    check(method(watcher, 18, HRESULT)(watcher))
        finally:
            api.close()
    deadline = time.monotonic() + 3
    while set(ComObject._live) != baseline and time.monotonic() < deadline:
        time.sleep(0.02)
    assert set(ComObject._live) == baseline


def test_typed_watcher_handler_identity_matches_the_sdk():
    from docking.platform.backends.windows.winrt_collections import event_iid

    assert (
        event_iid(
            "Windows.Devices.Enumeration.DeviceWatcher",
            mod._WATCHER,
            "Windows.Devices.Enumeration.DeviceInformation",
            mod._INFORMATION,
        )
        == "03c5a07b-990c-5d09-b0b8-5734eaa38222"
    )


def test_a_transient_native_initialization_failure_can_recover(broker, monkeypatch):
    client, state, _entered, _released = broker
    attempts = []

    class RetryRuntime(Runtime):
        def __enter__(self):
            attempts.append(True)
            if len(attempts) == 1:
                raise OSError("Native service restarting")
            return self

    monkeypatch.setattr(mod, "WindowsRuntime", RetryRuntime)
    with pytest.raises(OSError, match="restarting"):
        client.snapshot()
    assert client.snapshot().devices == state.records
    assert len(attempts) == 2
