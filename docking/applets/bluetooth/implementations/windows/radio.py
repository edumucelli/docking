"""Windows Bluetooth radios, classic inventory and owned discovery sessions.

WinRT supplies stable adapter identities and actual radio state, including off
and policy-disabled radios. Win32 cached device enumeration is scoped to each
local radio. Explicit discovery sessions issue bounded inquiries on that radio.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import threading
from collections.abc import Generator, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from docking.applets.bluetooth.implementations.windows.le import (
    LowEnergyClient,
    LowEnergyDevice,
    LowEnergyProvider,
)
from docking.applets.bluetooth.implementations.windows.pairing import PairingOperation
from docking.platform.native.windows.ffi import bind

_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p


class _RadioInfo(ctypes.Structure):
    _fields_ = [
        ("size", _DWORD),
        ("address", ctypes.c_uint64),
        ("name", ctypes.c_uint16 * 248),
        ("device_class", _DWORD),
        ("subversion", ctypes.c_uint16),
        ("manufacturer", ctypes.c_uint16),
    ]


class _DeviceInfo(ctypes.Structure):
    _fields_ = [
        ("size", _DWORD),
        ("address", ctypes.c_uint64),
        ("device_class", _DWORD),
        ("connected", _BOOL),
        ("remembered", _BOOL),
        ("authenticated", _BOOL),
        ("last_seen", ctypes.c_uint16 * 8),
        ("last_used", ctypes.c_uint16 * 8),
        ("name", ctypes.c_uint16 * 248),
    ]


class _Search(ctypes.Structure):
    _fields_ = [
        ("size", _DWORD),
        ("authenticated", _BOOL),
        ("remembered", _BOOL),
        ("unknown", _BOOL),
        ("connected", _BOOL),
        ("inquiry", _BOOL),
        ("timeout", ctypes.c_ubyte),
        ("radio", _HANDLE),
    ]


def address_text(address: int) -> str:
    text = f"{address:012x}"
    return ":".join(text[index : index + 2] for index in range(0, 12, 2))


@dataclass(frozen=True)
class ClassicDevice:
    address: int
    name: str
    device_class: int
    paired: bool
    connected: bool
    battery_percent: int | None = None
    rssi: int | None = None
    endpoint: LowEnergyDevice | None = None


@dataclass(frozen=True)
class Radio:
    identifier: str
    address: int
    name: str
    state: int
    devices: tuple[ClassicDevice | LowEnergyDevice, ...] = ()
    discovering: bool = False
    classic_supported: bool = True


class ClassicInventory:
    def __init__(self) -> None:
        dll = ctypes.WinDLL("bthprops.cpl", use_last_error=True)
        self._first_radio = bind(
            dll,
            "BluetoothFindFirstRadio",
            _HANDLE,
            ctypes.POINTER(_DWORD),
            ctypes.POINTER(_HANDLE),
        )
        self._next_radio = bind(
            dll, "BluetoothFindNextRadio", _BOOL, _HANDLE, ctypes.POINTER(_HANDLE)
        )
        self._close_radios = bind(dll, "BluetoothFindRadioClose", _BOOL, _HANDLE)
        self._info = bind(
            dll, "BluetoothGetRadioInfo", _DWORD, _HANDLE, ctypes.POINTER(_RadioInfo)
        )
        self._first_device = bind(
            dll,
            "BluetoothFindFirstDevice",
            _HANDLE,
            ctypes.POINTER(_Search),
            ctypes.POINTER(_DeviceInfo),
        )
        self._next_device = bind(
            dll,
            "BluetoothFindNextDevice",
            _BOOL,
            _HANDLE,
            ctypes.POINTER(_DeviceInfo),
        )
        self._close_devices = bind(dll, "BluetoothFindDeviceClose", _BOOL, _HANDLE)
        self._remove = bind(
            dll,
            "BluetoothRemoveDevice",
            _DWORD,
            ctypes.POINTER(ctypes.c_uint64),
        )
        self._close_handle = bind(
            ctypes.WinDLL("kernel32"), "CloseHandle", _BOOL, _HANDLE
        )

    @staticmethod
    def _end() -> None:
        error = ctypes.get_last_error()
        if error != 259:  # ERROR_NO_MORE_ITEMS, including no installed radio
            raise OSError(error, "Bluetooth inventory failed")

    def snapshot(
        self, *, inquiry_addresses: frozenset[int] = frozenset()
    ) -> dict[int, tuple[ClassicDevice, ...]]:
        if any(
            type(address) is not int or not 0 < address < 1 << 48
            for address in inquiry_addresses
        ):
            raise ValueError("Bluetooth inquiry requires valid radio addresses")
        result = {}
        with closing(self._radios()) as radios:
            for info, handle in radios:
                result[int(info.address)] = self._devices(
                    handle, inquiry=int(info.address) in inquiry_addresses
                )
        return result

    @contextmanager
    def radio_handle(self, address: int) -> Iterator[_HANDLE | None]:
        """Keep the selected radio alive for one operation, never all radios."""
        if type(address) is not int or not 0 < address < 1 << 48:
            raise ValueError("Bluetooth operation requires a valid radio address")
        with closing(self._radios()) as radios:
            for info, handle in radios:
                if info.address == address:
                    yield handle
                    return
        yield None

    def _radios(self) -> Generator[tuple[_RadioInfo, _HANDLE], None, None]:
        radio = _HANDLE()
        cursor = self._first_radio(ctypes.byref(_DWORD(4)), ctypes.byref(radio))
        try:
            if not cursor:
                self._end()
                return
            for _ in range(256):
                try:
                    info = _RadioInfo()
                    info.size = ctypes.sizeof(info)
                    status = self._info(radio, ctypes.byref(info))
                    if status:
                        raise OSError(status, "Bluetooth radio information failed")
                    yield info, radio
                finally:
                    if radio.value:
                        self._close_handle(radio)
                        radio = _HANDLE()
                if not self._next_radio(cursor, ctypes.byref(radio)):
                    self._end()
                    return
            raise OSError("Bluetooth radio enumeration exceeded its limit")
        finally:
            if radio.value:
                self._close_handle(radio)
            if cursor:
                self._close_radios(cursor)

    def _devices(self, radio, *, inquiry: bool = False) -> tuple[ClassicDevice, ...]:
        if inquiry and not radio.value:
            raise ValueError("Bluetooth inquiry requires a selected radio handle")
        search = _Search()
        search.size = ctypes.sizeof(search)
        search.authenticated = search.connected = 1
        search.inquiry = inquiry
        search.unknown = search.remembered = inquiry
        # The SDK expresses inquiry timeouts in 1.28-second units.
        search.timeout = 1 if inquiry else 0
        search.radio = radio.value
        info = _DeviceInfo()
        info.size = ctypes.sizeof(info)
        cursor = self._first_device(ctypes.byref(search), ctypes.byref(info))
        try:
            if not cursor:
                self._end()
                return ()
            result = {}
            for _ in range(4096):
                if 0 < info.address < 1 << 48 and (
                    inquiry or info.authenticated or info.connected
                ):
                    result[info.address] = ClassicDevice(
                        int(info.address),
                        bytes(info.name)
                        .decode("utf-16-le", errors="replace")
                        .split("\0", 1)[0],
                        int(info.device_class),
                        bool(info.authenticated),
                        bool(info.connected),
                    )
                info = _DeviceInfo()
                info.size = ctypes.sizeof(info)
                if not self._next_device(cursor, ctypes.byref(info)):
                    self._end()
                    return tuple(result.values())
            raise OSError("Bluetooth device enumeration exceeded its limit")
        finally:
            if cursor:
                self._close_devices(cursor)

    def remove_pairing(self, radio_address: int, device_address: int) -> bool:
        """Revalidate the remembered classic device before removing its bond."""
        if any(
            type(address) is not int or not 0 < address < 1 << 48
            for address in (radio_address, device_address)
        ):
            return False
        devices = self.snapshot().get(radio_address, ())
        if not any(d.address == device_address and d.paired for d in devices):
            return False
        status = self._remove(ctypes.byref(ctypes.c_uint64(device_address)))
        if status:
            raise OSError(status, "Bluetooth pairing removal failed")
        return True


_RADIO_SCRIPT = r"""
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);
Add-Type -AssemblyName System.Runtime.WindowsRuntime;
$null=[Windows.Devices.Bluetooth.BluetoothAdapter,Windows.Devices.Bluetooth,ContentType=WindowsRuntime];
$null=[Windows.Devices.Enumeration.DeviceInformation,Windows.Devices.Enumeration,ContentType=WindowsRuntime];
$null=[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime];
$asTask=([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
 $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
})[0];
function Await($operation,[Type]$type) {
 $task=$asTask.MakeGenericMethod($type).Invoke($null,@($operation));
 if(-not $task.Wait(3000)){throw 'Bluetooth operation timed out'};
 return $task.Result;
}
$json=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('REQUEST'));
$request=$json | ConvertFrom-Json;
$selector=[Windows.Devices.Bluetooth.BluetoothAdapter]::GetDeviceSelector();
$operation=[Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($selector);
$infos=Await $operation `
 ([Windows.Devices.Enumeration.DeviceInformationCollection]);
$entries=@();$changed=$false;
foreach($info in $infos) {
 $adapter=Await ([Windows.Devices.Bluetooth.BluetoothAdapter]::FromIdAsync($info.Id)) `
  ([Windows.Devices.Bluetooth.BluetoothAdapter]);
 if($null -eq $adapter){continue};
 $radio=Await ($adapter.GetRadioAsync()) ([Windows.Devices.Radios.Radio]);
 if($null -eq $radio -or [int]$radio.Kind -ne 2){continue};
 if($request.action -ceq 'power' -and $info.Id -ceq $request.id -and
    $adapter.BluetoothAddress -eq $request.address -and [int]$radio.State -in 1,2) {
  $access=Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) `
   ([Windows.Devices.Radios.RadioAccessStatus]);
  if([int]$access -eq 1) {
   $target=if($request.powered){1}else{2};
   $status=Await ($radio.SetStateAsync([Windows.Devices.Radios.RadioState]$target)) `
    ([Windows.Devices.Radios.RadioAccessStatus]);
   $changed=([int]$status -eq 1 -and [int]$radio.State -eq $target);
  }
 }
 $entries+=@{id=[string]$info.Id;address=[long]$adapter.BluetoothAddress;
  name=[string]$info.Name;state=[int]$radio.State;classic=[bool]$adapter.IsClassicSupported};
}
@{radios=@($entries);changed=$changed} | ConvertTo-Json -Depth 4 -Compress
"""


def _request(payload: dict[str, Any]) -> dict[str, Any]:
    data = base64.b64encode(json.dumps(payload).encode()).decode()
    script = _RADIO_SCRIPT.replace("REQUEST", data)
    executable = (
        Path(os.environ.get("SYSTEMROOT", "C:\\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    result = subprocess.run(
        [
            str(executable),
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            base64.b64encode(script.encode("utf-16-le")).decode(),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        timeout=8,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise OSError("Windows Bluetooth radio query failed")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise ValueError("Invalid Bluetooth response")
    return value


class WindowsBluetoothAPI:
    def __init__(self, *, low_energy: LowEnergyProvider | None = None) -> None:
        self._discovery_lock = threading.Lock()
        self._pairing_lock = threading.Lock()
        self._pairing: PairingOperation | None = None
        self._closed = threading.Event()
        self._discovering: dict[str, int] = {}
        self._pending: dict[str, threading.Event] = {}
        self._le = low_energy if low_energy is not None else LowEnergyClient()
        self._le_discovering: tuple[str, int] | None = None

    def close(self) -> None:
        self._closed.set()
        self._le.close()
        with self._pairing_lock:
            if self._pairing is not None:
                self._pairing.cancel()
        with self._discovery_lock:
            self._discovering.clear()
            for token in self._pending.values():
                token.set()
            self._pending.clear()

    def pair_device(
        self, radio: Radio, device: ClassicDevice | LowEnergyDevice, timeout_s: int
    ) -> bool:
        operation = PairingOperation()
        with self._pairing_lock:
            if self._closed.is_set() or self._pairing is not None or radio.state != 1:
                return False
            self._pairing = operation
        try:
            if isinstance(device, LowEnergyDevice):
                if not device.can_pair:
                    return False
                return (
                    operation.run(
                        radio.address,
                        device.address,
                        timeout_s,
                        endpoint=device.identifier,
                        adapter_id=radio.identifier,
                        address_type=device.address_type,
                    )
                    and not self._closed.is_set()
                )
            return (
                operation.run(radio.address, device.address, timeout_s)
                and not self._closed.is_set()
            )
        finally:
            with self._pairing_lock:
                if self._pairing is operation:
                    self._pairing = None

    def start_discovery(self, radio: Radio) -> bool:
        if self._closed.is_set() or radio.state != 1:
            return False
        with self._discovery_lock:
            if self._discovering.get(radio.identifier) == radio.address:
                return True
            if radio.identifier in self._pending:
                return False
            token = threading.Event()
            self._pending[radio.identifier] = token
        try:
            devices = (
                ClassicInventory().snapshot(
                    inquiry_addresses=frozenset((radio.address,))
                )
                if radio.classic_supported
                else {}
            )
            le = self._le.snapshot()
            le_started = False
            if (
                le.adapter_id == radio.identifier
                and le.adapter_address == radio.address
            ):
                le_started = self._le.discover(token, (radio.identifier, radio.address))
            with self._discovery_lock:
                if (
                    self._closed.is_set()
                    or self._pending.get(radio.identifier) is not token
                    or (radio.address not in devices and not le_started)
                ):
                    if le_started:
                        self._le.stop_discovery()
                    return False
                self._discovering[radio.identifier] = radio.address
                if le_started:
                    self._le_discovering = radio.identifier, radio.address
                return True
        finally:
            with self._discovery_lock:
                if self._pending.get(radio.identifier) is token:
                    self._pending.pop(radio.identifier)

    def stop_discovery(self, identifier: str) -> bool:
        with self._discovery_lock:
            self._discovering.pop(identifier, None)
            token = self._pending.pop(identifier, None)
            if token is not None:
                token.set()
            if (
                self._le_discovering is not None
                and self._le_discovering[0] == identifier
            ):
                self._le.stop_discovery()
                self._le_discovering = None
        return True

    def remove_pairing(
        self, radio: Radio, device: ClassicDevice | LowEnergyDevice
    ) -> bool:
        if self._closed.is_set():
            return False
        if isinstance(device, LowEnergyDevice):
            return self._le.unpair(device, (radio.identifier, radio.address))
        if device.endpoint is not None and device.endpoint.owned_connection:
            self._le.disconnect(device.endpoint, (radio.identifier, radio.address))
        return ClassicInventory().remove_pairing(radio.address, device.address)

    def connect_device(
        self, radio: Radio, device: ClassicDevice | LowEnergyDevice
    ) -> bool:
        target = device if isinstance(device, LowEnergyDevice) else device.endpoint
        if target is None:
            return False
        return not self._closed.is_set() and self._le.connect(
            target, (radio.identifier, radio.address)
        )

    def disconnect_device(
        self, radio: Radio, device: ClassicDevice | LowEnergyDevice
    ) -> bool:
        target = device if isinstance(device, LowEnergyDevice) else device.endpoint
        if target is None:
            return False
        return not self._closed.is_set() and self._le.disconnect(
            target, (radio.identifier, radio.address)
        )

    def snapshot(self) -> tuple[Radio, ...]:
        if self._closed.is_set():
            return ()
        data = _request({"action": "read"})
        entries = data.get("radios")
        if not isinstance(entries, list) or len(entries) > 256:
            raise ValueError("Invalid Bluetooth radios")
        result = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid Bluetooth radio")
            identifier, address = entry.get("id"), entry.get("address")
            name, state = entry.get("name"), entry.get("state")
            classic = entry.get("classic")
            if (
                not isinstance(identifier, str)
                or not 0 < len(identifier) <= 32768
                or "\0" in identifier
                or type(address) is not int
                or not 0 < address < 1 << 48
                or not isinstance(name, str)
                or type(state) is not int
                or state not in (0, 1, 2, 3)
                or type(classic) is not bool
            ):
                raise ValueError("Invalid Bluetooth radio properties")
            result[identifier] = Radio(
                identifier, address, name, state, classic_supported=classic
            )
        with self._discovery_lock:
            self._discovering = {
                identifier: address
                for identifier, address in self._discovering.items()
                if identifier in result
                and result[identifier].address == address
                and result[identifier].state == 1
            }
            active = dict(self._discovering)
            if (
                self._le_discovering is not None
                and active.get(self._le_discovering[0]) != self._le_discovering[1]
            ):
                self._le.stop_discovery()
                self._le.release_connections()
                self._le_discovering = None
        if self._closed.is_set():
            return ()
        inquiries = frozenset(
            r.address
            for r in result.values()
            if r.classic_supported and active.get(r.identifier) == r.address
        )
        devices = (
            ClassicInventory().snapshot(inquiry_addresses=inquiries)
            if inquiries
            else ClassicInventory().snapshot()
            if any(r.classic_supported for r in result.values())
            else {}
        )
        if self._closed.is_set():
            return ()
        le = self._le.snapshot() if result else None
        if le is None or not any(
            r.identifier == le.adapter_id
            and r.address == le.adapter_address
            and r.state == 1
            for r in result.values()
        ):
            # External power-off/removal also releases maintained connections
            # when Docking has no discovery session to invalidate.
            self._le.release_connections()
        with self._discovery_lock:
            if self._le_discovering is not None and (
                le is None
                or (le.adapter_id, le.adapter_address) != self._le_discovering
                or not le.discovering
            ):
                identifier, _address = self._le_discovering
                self._le.stop_discovery()
                self._le_discovering = None
                if identifier in result and not result[identifier].classic_supported:
                    self._discovering.pop(identifier, None)
            active = dict(self._discovering)
        output: list[Radio] = []
        for radio in result.values():
            endpoints = (
                le.devices
                if le is not None
                and le.adapter_id == radio.identifier
                and le.adapter_address == radio.address
                else ()
            )
            telemetry = {d.address: d for d in endpoints if not d.low_energy}
            scanning = active.get(radio.identifier) == radio.address
            classic = tuple(
                replace(
                    d,
                    battery_percent=telemetry[d.address].battery_percent,
                    rssi=telemetry[d.address].rssi,
                    endpoint=telemetry[d.address],
                    connected=(d.connected or telemetry[d.address].connected)
                    and radio.state not in (2, 3),
                )
                if d.address in telemetry
                else d
                for d in devices.get(radio.address, ())
                if scanning or d.paired or d.connected
            )
            low_energy = tuple(
                replace(
                    d,
                    connected=d.connected and radio.state == 1,
                    owned_connection=d.owned_connection and radio.state == 1,
                )
                for d in endpoints
                if d.low_energy
                and (d.paired or (radio.state == 1 and (scanning or d.connected)))
            )
            output.append(
                replace(radio, devices=classic + low_energy, discovering=scanning)
            )
        return tuple(output)

    def set_power(self, radio: Radio, powered: bool) -> bool:
        if self._closed.is_set():
            return False
        changed = (
            _request(
                {
                    "action": "power",
                    "id": radio.identifier,
                    "address": radio.address,
                    "powered": powered,
                }
            ).get("changed")
            is True
        )
        if changed and not powered:
            self.stop_discovery(radio.identifier)
            self._le.release_connections()
        return changed
