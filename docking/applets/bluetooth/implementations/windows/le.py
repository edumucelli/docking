"""WinRT Bluetooth endpoints, live discovery and owned GATT connections.

COM objects stay inside one MTA worker. Only immutable device records cross
into the shared applet; callbacks queue retained event arguments for that MTA.
"""

from __future__ import annotations

import ctypes
import queue
import re
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import ExitStack, suppress
from dataclasses import dataclass, field, replace
from typing import Protocol

from docking.applets.bluetooth.implementations.windows.advertisement import (
    RECEIVED_IID,
    WATCHER_IID,
    Advertisement,
    read_advertisement,
)
from docking.applets.bluetooth.implementations.windows.gatt import read_battery
from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    method,
    object_result,
    query,
    release,
    value,
)
from docking.platform.native.windows.com_callback import ComCallback
from docking.platform.native.windows.winrt import WindowsRuntime, check
from docking.platform.native.windows.winrt_collections import (
    StringIterable,
    event_iid,
    property_value,
)

_P = ctypes.c_void_p
_INFORMATION = "aba0fb95-4398-489d-8e44-e6130927011f"
_INFORMATION2 = GUID.parse("f156a638-7997-48d9-a10c-269d46533f48")
_STATICS = GUID.parse("493b4f34-a84f-45fd-9167-15d1cb1bd1f9")
_WATCHER = "c9eab97d-8f6b-4f96-a9f4-abc814e22271"
_UPDATE = "8f315305-d972-44b7-a37e-9e822c78213b"
_PAIRING2 = GUID.parse("f68612fd-0aee-4328-85cc-1c742bb1790d")
_CUSTOM = "85138c02-4ee6-4914-8370-107a39144c0e"
_REQUESTED = "f717fc56-de6b-487f-8376-0180aca69963"
_LE_FACTORY = GUID.parse("5f12c06b-3bac-43e8-ad16-563271bd41c2")
_ADAPTER = GUID.parse("8b02fb6a-ac4c-4741-8661-8eab7d17ea9f")
_DEVICE_ID = GUID.parse("a7884e67-3efb-4f31-bbc2-810e09977404")
_GATT = GUID.parse("2e65b95c-539f-4db7-82a8-73bdbbf73ebf")
_AUDIO = GUID.parse("e60963a2-69e6-5ffc-9e13-824a85213daf")
_CLOSABLE = GUID.parse("30d5a829-7fa4-4026-83bb-d75bae4ea99e")
_ACTIVATION = GUID.parse("00000035-0000-0000-c000-000000000046")
_PREFIX = "System.Devices.Aep."
_CLASSIC_PROTOCOL = "e0cbf06c-cd8b-4647-bb8a-263b43f0f974"
_LE_PROTOCOL = "bb7bb05e-5972-42b5-94fc-76eaa7084d49"
_CLASSIC_PAIRED = (
    '(System.Devices.Aep.ProtocolId:="{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}" '
    "AND System.Devices.Aep.IsPaired:=System.StructuredQueryType.Boolean#True)"
)
_PROPERTIES = tuple(
    _PREFIX + name
    for name in (
        "DeviceAddress",
        "ProtocolId",
        "ContainerId",
        "IsConnected",
        "IsPaired",
        "IsPresent",
        "CanPair",
        "SignalStrength",
        "Bluetooth.Le.AddressType",
        "Bluetooth.Le.Appearance",
    )
)
_ADDRESS = re.compile(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")


@dataclass(frozen=True)
class LowEnergyDevice:
    identifier: str
    address: int
    name: str
    paired: bool
    connected: bool
    can_pair: bool
    address_type: int | None = None
    appearance: int | None = None
    rssi: int | None = None
    battery_percent: int | None = None
    owned_connection: bool = False
    low_energy: bool = True
    audio_endpoint: str = ""


@dataclass(frozen=True)
class LowEnergySnapshot:
    adapter_id: str
    adapter_address: int
    devices: tuple[LowEnergyDevice, ...]
    discovering: bool = False


@dataclass
class _GattConnection:
    scope: ExitStack
    session: ctypes.c_void_p
    remote: ctypes.c_void_p
    battery_at: float = 0.0
    battery_percent: int | None = None


class LowEnergyProvider(Protocol):
    def snapshot(self) -> LowEnergySnapshot: ...
    def close(self) -> None: ...
    def discover(
        self, cancelled: threading.Event, adapter: tuple[str, int]
    ) -> bool: ...
    def stop_discovery(self) -> None: ...
    def release_connections(self) -> None: ...
    def unpair(self, device: LowEnergyDevice, adapter: tuple[str, int]) -> bool: ...
    def connect(self, device: LowEnergyDevice, adapter: tuple[str, int]) -> bool: ...
    def disconnect(self, device: LowEnergyDevice, adapter: tuple[str, int]) -> bool: ...


def _integer(raw: object, minimum: int, maximum: int) -> int | None:
    return raw if type(raw) is int and minimum <= raw <= maximum else None


class EndpointAPI:
    """All methods and retained interfaces belong to the entering MTA worker."""

    def __init__(self, runtime: WindowsRuntime, cancelled: threading.Event):
        self.runtime, self.cancelled = runtime, cancelled
        self._scope = ExitStack()
        with self._scope as initial:
            self._statics = runtime.factory(
                self._scope, "Windows.Devices.Enumeration.DeviceInformation", _STATICS
            )
            self._events: queue.Queue[tuple[int, str, int]] = queue.Queue(maxsize=4096)
            self._overflow = threading.Event()
            self._event_lock = threading.Lock()
            self._watch_scope = ExitStack()
            self._watcher = _P()
            self._advertisement_watcher = _P()
            self._advertisements: dict[
                tuple[int, int], tuple[float, Advertisement]
            ] = {}
            self._epoch, self._started_at = 0, 0.0
            self._discovering = False
            self._adapter: tuple[str, int] = ("", 0)
            self._records: dict[str, tuple[ExitStack, ctypes.c_void_p]] = {}
            self._connections: dict[str, _GattConnection] = {}
            self._battery_at = 0.0
            self._batteries: dict[str, int] = {}
            self._audio_endpoints: dict[str, str] = {}
            self._audio_connections: dict[str, tuple[ExitStack, ctypes.c_void_p]] = {}
            self._adapter = self.default_adapter()
            self._scope = initial.pop_all()

    def _async(
        self, scope, pointer, index, *arguments, timeout: float = 3
    ) -> ctypes.c_void_p:
        operation = object_result(scope, pointer, index, *arguments)
        self.runtime.wait(scope, operation, self.cancelled, timeout=timeout)
        return object_result(scope, operation, 8)

    def _close(self, pointer) -> None:
        with ExitStack() as scope:
            closable = query(scope, pointer, _CLOSABLE)
            check(method(closable, 6, HRESULT)(closable))

    def _subscribe(self, scope, pointer, index, iid, callback) -> None:
        handler = ComCallback(
            iid,
            (_P, _P),
            callback,
            on_error=lambda _error: self._overflow.set(),
            agile=True,
        )
        token = ctypes.c_int64()
        try:
            check(
                method(pointer, index, HRESULT, _P, ctypes.POINTER(ctypes.c_int64))(
                    pointer, handler.pointer, ctypes.byref(token)
                )
            )
        finally:
            handler.close()

        def remove():
            with suppress(OSError):
                check(
                    method(pointer, index + 1, HRESULT, ctypes.c_int64)(
                        pointer, token.value
                    )
                )

        scope.callback(remove)

    def _stop_watch(self) -> None:
        with self._event_lock:
            self._epoch += 1
        self._discovering = False
        if self._advertisement_watcher.value:
            with suppress(OSError):
                check(
                    method(self._advertisement_watcher, 18, HRESULT)(
                        self._advertisement_watcher
                    )
                )
        if self._watcher.value:
            with suppress(OSError):
                check(method(self._watcher, 18, HRESULT)(self._watcher))
        self._watch_scope.close()
        self._watch_scope, self._watcher = ExitStack(), _P()
        self._advertisement_watcher = _P()
        self._advertisements.clear()
        self._drain()

    def watch(self, discovering: bool) -> None:
        self._stop_watch()
        for scope, _pointer in self._records.values():
            scope.close()
        self._records.clear()
        epoch = self._epoch
        scope = self._watch_scope
        properties = StringIterable(self.runtime, _PROPERTIES)
        try:
            le_protocol = f'(System.Devices.Aep.ProtocolId:="{{{_LE_PROTOCOL}}}")'
            if discovering:
                selector = le_protocol + " OR " + _CLASSIC_PAIRED
            else:
                known = (
                    "(System.Devices.Aep.IsPaired:="
                    "System.StructuredQueryType.Boolean#True OR "
                    "System.Devices.Aep.IsConnected:="
                    "System.StructuredQueryType.Boolean#True)"
                )
                selector = (
                    "(" + le_protocol + " AND " + known + ") OR " + _CLASSIC_PAIRED
                )
            with self.runtime.string(selector) as text:
                self._watcher = object_result(
                    scope,
                    self._statics,
                    9,
                    _P,
                    text,
                    _P,
                    properties.pointer,
                    ctypes.c_int32,
                    5,
                )
        finally:
            properties.close()

        def enqueue(kind, argument):
            with self._event_lock:
                if epoch != self._epoch:
                    return
                pointer = _P(argument)
                method(pointer, 1, ctypes.c_uint32)(pointer)
                try:
                    self._events.put_nowait((epoch, kind, int(argument)))
                except queue.Full:
                    release(pointer)
                    self._overflow.set()

        for index, kind, name, iid in (
            (6, "added", "DeviceInformation", _INFORMATION),
            (8, "updated", "DeviceInformationUpdate", _UPDATE),
            (10, "removed", "DeviceInformationUpdate", _UPDATE),
        ):
            self._subscribe(
                scope,
                self._watcher,
                index,
                event_iid(
                    "Windows.Devices.Enumeration.DeviceWatcher",
                    _WATCHER,
                    "Windows.Devices.Enumeration." + name,
                    iid,
                ),
                lambda _sender, argument, kind=kind: enqueue(kind, argument),
            )
        check(method(self._watcher, 17, HRESULT)(self._watcher))
        if discovering and self._adapter[0]:
            with suppress(OSError):
                self._start_advertisements(scope, enqueue)
        self._discovering, self._started_at = discovering, time.monotonic()

    def _start_advertisements(self, scope: ExitStack, enqueue) -> None:
        factory = self.runtime.factory(
            scope,
            "Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher",
            _ACTIVATION,
        )
        self._advertisement_watcher = query(
            scope, object_result(scope, factory, 6), GUID.parse(WATCHER_IID)
        )
        self._subscribe(
            scope,
            self._advertisement_watcher,
            19,
            event_iid(
                "Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher",
                WATCHER_IID,
                "Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementReceivedEventArgs",
                RECEIVED_IID,
            ),
            lambda _sender, argument: enqueue("advertisement", argument),
        )
        # Active scanning includes scan responses carrying the local name.
        check(
            method(self._advertisement_watcher, 12, HRESULT, ctypes.c_int32)(
                self._advertisement_watcher, 1
            )
        )
        check(
            method(self._advertisement_watcher, 17, HRESULT)(
                self._advertisement_watcher
            )
        )

    def _drain(self) -> None:
        while True:
            try:
                epoch, kind, address = self._events.get_nowait()
            except queue.Empty:
                return
            pointer = _P(address)
            try:
                if epoch != self._epoch:
                    continue
                if kind == "advertisement":
                    observation = None
                    with suppress(OSError):
                        observation = read_advertisement(self.runtime, pointer)
                    if observation is not None:
                        key = observation.address, observation.address_type
                        previous_observation = self._advertisements.get(key)
                        if not observation.name and previous_observation is not None:
                            observation = replace(
                                observation, name=previous_observation[1].name
                            )
                        self._advertisements[key] = time.monotonic(), observation
                        if len(self._advertisements) > 4096:
                            self._advertisements.pop(next(iter(self._advertisements)))
                    continue
                identifier = self.runtime.text(pointer, 6)
                if not identifier or len(identifier) > 32768 or "\0" in identifier:
                    continue
                previous = self._records.get(identifier)
                if kind == "removed":
                    connection = self._connections.pop(identifier, None)
                    if connection is not None:
                        with suppress(OSError):
                            connection.scope.close()
                    audio = self._audio_connections.pop(identifier, None)
                    if audio is not None:
                        with suppress(OSError):
                            audio[0].close()
                    if previous is not None:
                        previous[0].close()
                        self._records.pop(identifier)
                elif kind == "updated":
                    if previous is not None:
                        check(
                            method(previous[1], 12, HRESULT, _P)(previous[1], pointer)
                        )
                elif kind == "added":
                    method(pointer, 1, ctypes.c_uint32)(pointer)
                    retained = ExitStack()
                    retained.callback(release, pointer)
                    self._records[identifier] = retained, pointer
                    if previous is not None:
                        previous[0].close()
                    if len(self._records) > 4096:
                        raise OSError(
                            "Bluetooth endpoint collection exceeded its limit"
                        )
            finally:
                release(pointer)

    def record(self, pointer) -> LowEnergyDevice | None:
        with ExitStack() as scope:
            properties = object_result(scope, pointer, 11)
            address = property_value(
                self.runtime, properties, _PREFIX + "DeviceAddress"
            )
            if not isinstance(address, str) or not _ADDRESS.fullmatch(address):
                return None
            numeric = int(address.replace(":", ""), 16)
            if not numeric:
                return None
            info = query(scope, pointer, _INFORMATION2)
            pairing = object_result(scope, info, 7)
            identifier = self.runtime.text(pointer, 6)
            protocol = property_value(self.runtime, properties, _PREFIX + "ProtocolId")
            if protocol not in (_CLASSIC_PROTOCOL, _LE_PROTOCOL):
                return None
            container = property_value(
                self.runtime, properties, _PREFIX + "ContainerId"
            )
            connection = self._connections.get(identifier)
            audio = self._audio_connections.get(identifier)
            active = (
                connection is not None
                and value(connection.session, 11, ctypes.c_int32).value == 1
            )
            active = active or (
                audio is not None and value(audio[1], 9, ctypes.c_int32).value == 1
            )
            battery = (
                self._batteries.get(container) if isinstance(container, str) else None
            )
            if battery is None and connection is not None and active:
                battery = connection.battery_percent
            return LowEnergyDevice(
                identifier,
                numeric,
                self.runtime.text(pointer, 7).replace("\0", ""),
                bool(value(pairing, 6, ctypes.c_ubyte).value),
                property_value(self.runtime, properties, _PREFIX + "IsConnected")
                is True
                or active,
                bool(value(pairing, 7, ctypes.c_ubyte).value),
                _integer(
                    property_value(
                        self.runtime, properties, _PREFIX + "Bluetooth.Le.AddressType"
                    ),
                    0,
                    2,
                ),
                _integer(
                    property_value(
                        self.runtime, properties, _PREFIX + "Bluetooth.Le.Appearance"
                    ),
                    0,
                    65535,
                ),
                _integer(
                    property_value(
                        self.runtime, properties, _PREFIX + "SignalStrength"
                    ),
                    -127,
                    20,
                ),
                battery_percent=battery,
                owned_connection=connection is not None or audio is not None,
                low_energy=protocol == _LE_PROTOCOL,
                audio_endpoint=self._audio_endpoints.get(container, "")
                if isinstance(container, str)
                else "",
            )

    def _read_batteries(self) -> None:
        if time.monotonic() - self._battery_at < 30:
            return
        self._battery_at = time.monotonic()
        properties = StringIterable(
            self.runtime,
            (
                "System.Devices.BatteryLife",
                "System.Devices.Connected",
                "System.Devices.LocalMachine",
            ),
        )
        batteries: dict[str, int] = {}
        try:
            with ExitStack() as scope, self.runtime.string("") as selector:
                collection = self._async(
                    scope,
                    self._statics,
                    8,
                    _P,
                    selector,
                    _P,
                    properties.pointer,
                    ctypes.c_int32,
                    2,
                )
                size = value(collection, 7, ctypes.c_uint32).value
                if size > 4096:
                    raise OSError("Bluetooth battery collection exceeded its limit")
                for index in range(size):
                    with ExitStack() as item_scope:
                        info = object_result(
                            item_scope, collection, 6, ctypes.c_uint32, index
                        )
                        props = object_result(item_scope, info, 11)
                        if (
                            property_value(
                                self.runtime, props, "System.Devices.Connected"
                            )
                            is not True
                            or property_value(
                                self.runtime, props, "System.Devices.LocalMachine"
                            )
                            is not False
                        ):
                            continue
                        percent = _integer(
                            property_value(
                                self.runtime, props, "System.Devices.BatteryLife"
                            ),
                            0,
                            100,
                        )
                        if percent is not None:
                            try:
                                identity = uuid.UUID(self.runtime.text(info, 6))
                            except ValueError:
                                continue
                            if identity.int:
                                batteries[str(identity)] = percent
        except OSError:
            pass  # Optional accessory telemetry must not hide the devices.
        finally:
            properties.close()
            self._batteries = batteries

    def _read_audio_endpoints(self) -> None:
        properties = StringIterable(
            self.runtime,
            ("System.Devices.ContainerId", "System.Devices.Aep.ContainerId"),
        )
        endpoints: dict[str, str] = {}
        try:
            with ExitStack() as scope:
                factory = self.runtime.factory(
                    scope, "Windows.Media.Audio.AudioPlaybackConnection", _AUDIO
                )
                selector = self.runtime.text(factory, 6)
                with self.runtime.string(selector) as text:
                    collection = self._async(
                        scope,
                        self._statics,
                        8,
                        _P,
                        text,
                        _P,
                        properties.pointer,
                        ctypes.c_int32,
                        1,
                    )
                size = value(collection, 7, ctypes.c_uint32).value
                if size > 4096:
                    raise OSError("Bluetooth audio collection exceeded its limit")
                for index in range(size):
                    with ExitStack() as item_scope:
                        info = object_result(
                            item_scope, collection, 6, ctypes.c_uint32, index
                        )
                        props = object_result(item_scope, info, 11)
                        container = property_value(
                            self.runtime, props, "System.Devices.ContainerId"
                        )
                        if container is None:
                            container = property_value(
                                self.runtime, props, "System.Devices.Aep.ContainerId"
                            )
                        if isinstance(container, str):
                            endpoints[container] = self.runtime.text(info, 6)
        except OSError:
            pass
        finally:
            properties.close()
            self._audio_endpoints = endpoints

    def default_adapter(self) -> tuple[str, int]:
        with ExitStack() as scope:
            factory = self.runtime.factory(
                scope, "Windows.Devices.Bluetooth.BluetoothAdapter", _ADAPTER
            )
            adapter = self._async(scope, factory, 8)
            return (
                (
                    self.runtime.text(adapter, 6),
                    int(value(adapter, 7, ctypes.c_uint64).value),
                )
                if adapter.value and value(adapter, 9, ctypes.c_ubyte).value
                else ("", 0)
            )

    def snapshot(self) -> LowEnergySnapshot:
        identity = self.default_adapter()
        if identity != self._adapter:
            self._stop_watch()
            self._discovering = False
            self.release_connections()
            for scope, _pointer in self._records.values():
                scope.close()
            self._records.clear()
            self._adapter = identity
        if not identity[0]:
            return LowEnergySnapshot("", 0, ())
        self._read_batteries()
        self._read_audio_endpoints()
        # At most one owned accessory read per poll, with a shared 1.5s budget.
        # Inventory never opens a BLE device or establishes a connection.
        due = sorted(self._connections.values(), key=lambda c: c.battery_at)
        for connection in due:
            if time.monotonic() - connection.battery_at < 30:
                break
            connection.battery_at = time.monotonic()
            connection.battery_percent = (
                read_battery(self.runtime, connection.remote, self.cancelled)
                if value(connection.session, 11, ctypes.c_int32).value == 1
                else None
            )
            break
        return LowEnergySnapshot(*identity, self.devices(), self._discovering)

    def devices(self) -> tuple[LowEnergyDevice, ...]:
        if self._overflow.is_set():
            self._overflow.clear()
            self.watch(self._discovering)
        elif not self._watcher.value or (
            self._discovering and time.monotonic() - self._started_at >= 30
        ):
            self.watch(self._discovering)
        self._drain()
        if value(self._watcher, 16, ctypes.c_int32).value == 5:
            raise OSError("Bluetooth endpoint watcher aborted")
        endpoints = tuple(
            device
            for _scope, pointer in self._records.values()
            if (device := self.record(pointer)) is not None
            and (self._discovering or device.paired or device.connected)
        )
        return self._merge_advertisements(endpoints)

    def _merge_advertisements(
        self, endpoints: tuple[LowEnergyDevice, ...]
    ) -> tuple[LowEnergyDevice, ...]:
        if not self._discovering:
            return endpoints
        now = time.monotonic()
        self._advertisements = {
            key: observation
            for key, observation in self._advertisements.items()
            if now - observation[0] < 45
        }
        unmatched = {key: entry[1] for key, entry in self._advertisements.items()}
        devices = []
        for device in endpoints:
            observation = (
                unmatched.pop((device.address, device.address_type), None)
                if device.low_energy and device.address_type in (0, 1)
                else None
            )
            if observation is not None:
                device = replace(
                    device,
                    name=device.name or observation.name,
                    rssi=observation.rssi
                    if observation.rssi is not None
                    else device.rssi,
                )
            devices.append(device)
        for observation in unmatched.values():
            # An advertisement is not a pairable DeviceInformation endpoint.
            # Its address type is part of its identity; never guess a bond.
            devices.append(
                LowEnergyDevice(
                    f"advertisement:{self._adapter[0]}:"
                    f"{observation.address:012x}:{observation.address_type}",
                    observation.address,
                    observation.name,
                    paired=False,
                    connected=False,
                    can_pair=False,
                    address_type=observation.address_type,
                    rssi=observation.rssi,
                )
            )
        return tuple(devices)

    def lookup(
        self, scope, device: LowEnergyDevice
    ) -> tuple[ctypes.c_void_p, LowEnergyDevice]:
        properties = StringIterable(self.runtime, _PROPERTIES)
        try:
            with self.runtime.string(device.identifier) as identifier:
                pointer = self._async(
                    scope,
                    self._statics,
                    7,
                    _P,
                    identifier,
                    _P,
                    properties.pointer,
                    ctypes.c_int32,
                    5,
                )
        finally:
            properties.close()
        fresh = self.record(pointer)
        if (
            fresh is None
            or fresh.identifier != device.identifier
            or fresh.address != device.address
            or fresh.low_energy != device.low_energy
            or (
                device.address_type is not None
                and fresh.address_type != device.address_type
            )
        ):
            raise OSError("Bluetooth endpoint identity changed")
        return pointer, fresh

    def unpair(self, device: LowEnergyDevice) -> bool:
        self.disconnect(device)
        with ExitStack() as scope:
            pointer, fresh = self.lookup(scope, device)
            if not fresh.paired:
                return False
            pairing = object_result(scope, query(scope, pointer, _INFORMATION2), 7)
            modern = query(scope, pairing, _PAIRING2)
            result = self._async(scope, modern, 9, timeout=5)
            if value(result, 6, ctypes.c_int32).value not in (0, 1):
                return False
            return not self.lookup(scope, device)[1].paired

    def pair(
        self,
        device: LowEnergyDevice,
        timeout: int,
        prompt: Callable[[int, str], str | None],
    ) -> bool:
        with ExitStack() as scope:
            pointer, fresh = self.lookup(scope, device)
            if fresh.paired:
                return True
            if not fresh.can_pair:
                return False
            pairing = object_result(scope, query(scope, pointer, _INFORMATION2), 7)
            custom = object_result(scope, query(scope, pairing, _PAIRING2), 7)

            def requested(_sender, args):
                event = _P(args)
                kind = value(event, 7, ctypes.c_int32).value
                pin = self.runtime.text(event, 8) if kind in (2, 8) else ""
                answer = None if self.cancelled.is_set() else prompt(kind, pin)
                if answer is None or self.cancelled.is_set():
                    return
                if kind == 4:
                    with self.runtime.string(answer) as text:
                        check(method(event, 10, HRESULT, _P)(event, text))
                elif kind in (1, 2, 8):
                    check(method(event, 9, HRESULT)(event))

            self._subscribe(
                scope,
                custom,
                9,
                event_iid(
                    "Windows.Devices.Enumeration.DeviceInformationCustomPairing",
                    _CUSTOM,
                    "Windows.Devices.Enumeration.DevicePairingRequestedEventArgs",
                    _REQUESTED,
                ),
                requested,
            )
            result = self._async(scope, custom, 6, ctypes.c_int32, 15, timeout=timeout)
            return (
                value(result, 6, ctypes.c_int32).value in (0, 3)
                and self.lookup(scope, device)[1].paired
            )

    def connect(self, device: LowEnergyDevice) -> bool:
        if not device.low_energy:
            return self._connect_audio(device)
        scope = ExitStack()
        try:
            _pointer, fresh = self.lookup(scope, device)
            if not fresh.paired:
                return False
            previous = self._connections.get(device.identifier)
            if (
                previous is not None
                and value(previous.session, 11, ctypes.c_int32).value == 1
            ):
                return True
            factory = self.runtime.factory(
                scope, "Windows.Devices.Bluetooth.BluetoothLEDevice", _LE_FACTORY
            )
            # FromIdAsync has a UI-thread consent contract. The address/type
            # overload supports this worker; verify its full returned endpoint
            # identity before maintaining any connection.
            remote = self._async(
                scope,
                factory,
                12,
                ctypes.c_uint64,
                fresh.address,
                ctypes.c_int32,
                fresh.address_type if fresh.address_type in (0, 1) else 2,
            )
            if not remote.value:
                return False
            scope.callback(self._close, remote)
            remote_id = self.runtime.text(remote, 6)
            if (
                remote_id != fresh.identifier
                or value(remote, 10, ctypes.c_uint64).value != fresh.address
            ):
                return False
            ids = self.runtime.factory(
                scope, "Windows.Devices.Bluetooth.BluetoothDeviceId", _DEVICE_ID
            )
            with self.runtime.string(remote_id) as identifier:
                native_id = object_result(scope, ids, 6, _P, identifier)
            sessions = self.runtime.factory(
                scope,
                "Windows.Devices.Bluetooth.GenericAttributeProfile.GattSession",
                _GATT,
            )
            session = self._async(scope, sessions, 6, _P, native_id)
            scope.callback(self._release_session, session)
            if not value(session, 7, ctypes.c_ubyte).value:
                return False
            check(method(session, 8, HRESULT, ctypes.c_ubyte)(session, True))
            deadline = time.monotonic() + 5
            while not self.cancelled.is_set() and time.monotonic() < deadline:
                if value(session, 11, ctypes.c_int32).value == 1:
                    previous = self._connections.pop(device.identifier, None)
                    if previous is not None:
                        previous.scope.close()
                    self._connections[device.identifier] = _GattConnection(
                        scope.pop_all(), session, remote
                    )
                    return True
                self.cancelled.wait(0.05)
            return False
        finally:
            scope.close()

    def disconnect(self, device: LowEnergyDevice) -> bool:
        if not device.low_energy:
            previous = self._audio_connections.pop(device.identifier, None)
            if previous is None:
                return False
            previous[0].close()
            return True
        previous = self._connections.pop(device.identifier, None)
        if previous is None:
            return False
        scope, session = previous.scope, previous.session
        try:
            check(method(session, 8, HRESULT, ctypes.c_ubyte)(session, False))
            return not bool(value(session, 9, ctypes.c_ubyte).value)
        finally:
            scope.close()

    def _release_session(self, session) -> None:
        try:
            check(method(session, 8, HRESULT, ctypes.c_ubyte)(session, False))
        finally:
            self._close(session)

    def _connect_audio(self, device: LowEnergyDevice) -> bool:
        with ExitStack() as scope:
            _pointer, fresh = self.lookup(scope, device)
            if (
                not fresh.paired
                or not fresh.audio_endpoint
                or fresh.audio_endpoint != device.audio_endpoint
            ):
                return False
            previous = self._audio_connections.get(device.identifier)
            if (
                previous is not None
                and value(previous[1], 9, ctypes.c_int32).value == 1
            ):
                return True
            factory = self.runtime.factory(
                scope, "Windows.Media.Audio.AudioPlaybackConnection", _AUDIO
            )
            with self.runtime.string(fresh.audio_endpoint) as identifier:
                connection = object_result(scope, factory, 7, _P, identifier)
            if not connection.value:
                return False
            scope.callback(self._close, connection)
            operation = object_result(scope, connection, 7)
            self.runtime.wait(scope, operation, self.cancelled, timeout=3)
            check(method(operation, 7, HRESULT)(operation))  # IAsyncAction.GetResults
            result = self._async(scope, connection, 11, timeout=5)
            if (
                value(result, 6, ctypes.c_int32).value != 0
                or value(connection, 9, ctypes.c_int32).value != 1
                or self.cancelled.is_set()
            ):
                return False
            previous = self._audio_connections.pop(device.identifier, None)
            if previous is not None:
                previous[0].close()
            self._audio_connections[device.identifier] = scope.pop_all(), connection
            return True

    def release_connections(self) -> None:
        for connection in self._connections.values():
            with suppress(OSError):
                connection.scope.close()
        self._connections.clear()
        for scope, _pointer in self._audio_connections.values():
            with suppress(OSError):
                scope.close()
        self._audio_connections.clear()

    def close(self) -> None:
        self._stop_watch()
        self.release_connections()
        for scope, _pointer in self._records.values():
            scope.close()
        self._records.clear()
        self._scope.close()


@dataclass
class _Request:
    action: str
    device: LowEnergyDevice | None = None
    discovering: bool = False
    complete: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    result: LowEnergySnapshot | bool = False
    error: OSError | ValueError | None = None
    adapter: tuple[str, int] | None = None


class LowEnergyClient:
    """Serialize all native endpoint work and cancel shutdown without GTK IO."""

    def __init__(self):
        self._closed = threading.Event()
        self._requests: queue.Queue[_Request] = queue.Queue(maxsize=16)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._current: _Request | None = None
        self._discovering = threading.Event()
        self._release_requested = threading.Event()

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            if self._current is not None:
                self._current.cancelled.set()

    def _call(
        self,
        action: str,
        device: LowEnergyDevice | None = None,
        *,
        discovering: bool = False,
        cancelled: threading.Event | None = None,
        adapter: tuple[str, int] | None = None,
    ):
        request = _Request(action, device, discovering)
        request.adapter = adapter
        if cancelled is not None:
            request.cancelled = cancelled
        with self._lock:
            if self._closed.is_set():
                return False
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._serve, name="bluetooth-le", daemon=True
                )
                self._thread.start()
            try:
                self._requests.put_nowait(request)
            except queue.Full:
                return False
        deadline = time.monotonic() + 12
        while not request.complete.wait(0.05):
            if self._closed.is_set() or time.monotonic() >= deadline:
                request.cancelled.set()
                return False
        if self._closed.is_set() or request.cancelled.is_set():
            return False
        if request.error is not None:
            raise request.error
        return request.result if not self._closed.is_set() else False

    def snapshot(self) -> LowEnergySnapshot:
        result = self._call("snapshot")
        return (
            result
            if isinstance(result, LowEnergySnapshot)
            else LowEnergySnapshot("", 0, ())
        )

    def discover(self, cancelled: threading.Event, adapter: tuple[str, int]) -> bool:
        if cancelled.is_set():
            return False
        self._discovering.set()
        return (
            self._call(
                "discover", discovering=True, cancelled=cancelled, adapter=adapter
            )
            is True
        )

    def stop_discovery(self) -> None:
        self._discovering.clear()

    def release_connections(self) -> None:
        self._discovering.clear()
        self._release_requested.set()

    def unpair(self, device: LowEnergyDevice, adapter: tuple[str, int]) -> bool:
        return self._call("unpair", device, adapter=adapter) is True

    def connect(self, device: LowEnergyDevice, adapter: tuple[str, int]) -> bool:
        return self._call("connect", device, adapter=adapter) is True

    def disconnect(self, device: LowEnergyDevice, adapter: tuple[str, int]) -> bool:
        return self._call("disconnect", device, adapter=adapter) is True

    def _serve(self):
        try:
            with WindowsRuntime() as runtime:
                api = EndpointAPI(runtime, self._closed)
                try:
                    while not self._closed.is_set():
                        if api._discovering and not self._discovering.is_set():
                            with suppress(OSError, ValueError):
                                api.watch(False)
                        if self._release_requested.is_set():
                            self._release_requested.clear()
                            api.release_connections()
                        try:
                            request = self._requests.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        try:
                            with self._lock:
                                self._current = request
                                if self._closed.is_set():
                                    request.cancelled.set()
                            api.cancelled = request.cancelled
                            if request.cancelled.is_set():
                                continue
                            if (
                                request.adapter is not None
                                and api.default_adapter() != request.adapter
                            ):
                                continue
                            if request.action == "snapshot":
                                request.result = api.snapshot()
                            elif request.action == "discover":
                                if not self._discovering.is_set():
                                    continue
                                api.watch(request.discovering)
                                request.result = (
                                    not request.cancelled.is_set()
                                    and self._discovering.is_set()
                                )
                                if not request.result:
                                    self._discovering.clear()
                                    api.watch(False)
                            elif request.device is not None:
                                if request.action == "unpair":
                                    request.result = api.unpair(request.device)
                                elif request.action == "connect":
                                    request.result = api.connect(request.device)
                                elif request.action == "disconnect":
                                    request.result = api.disconnect(request.device)
                        except (OSError, ValueError) as exc:
                            request.error = exc
                        finally:
                            with self._lock:
                                self._current = None
                            request.complete.set()
                finally:
                    api.close()
        except OSError as exc:
            with self._lock:
                while True:
                    try:
                        request = self._requests.get_nowait()
                    except queue.Empty:
                        break
                    request.error = exc
                    request.complete.set()
                self._thread = None
