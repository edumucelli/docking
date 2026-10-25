"""Windows Bluetooth radios and cached classic-device inventory.

WinRT supplies stable adapter identities and actual radio state, including off
and policy-disabled radios. Win32 cached device enumeration is scoped to each
local radio. It never starts an inquiry, pairs, connects or installs services.
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docking.platform.backends.windows.ffi import bind

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


@dataclass(frozen=True)
class Radio:
    identifier: str
    address: int
    name: str
    state: int
    devices: tuple[ClassicDevice, ...] = ()


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
        self._close_handle = bind(
            ctypes.WinDLL("kernel32"), "CloseHandle", _BOOL, _HANDLE
        )

    @staticmethod
    def _end() -> None:
        error = ctypes.get_last_error()
        if error != 259:  # ERROR_NO_MORE_ITEMS, including no installed radio
            raise OSError(error, "Bluetooth inventory failed")

    def snapshot(self) -> dict[int, tuple[ClassicDevice, ...]]:
        radio = _HANDLE()
        cursor = self._first_radio(ctypes.byref(_DWORD(4)), ctypes.byref(radio))
        try:
            if not cursor:
                self._end()
                return {}
            result = {}
            for _ in range(256):
                try:
                    info = _RadioInfo()
                    info.size = ctypes.sizeof(info)
                    status = self._info(radio, ctypes.byref(info))
                    if status:
                        raise OSError(status, "Bluetooth radio information failed")
                    result[int(info.address)] = self._devices(radio)
                finally:
                    if radio.value:
                        self._close_handle(radio)
                        radio = _HANDLE()
                if not self._next_radio(cursor, ctypes.byref(radio)):
                    self._end()
                    return result
            raise OSError("Bluetooth radio enumeration exceeded its limit")
        finally:
            if radio.value:
                self._close_handle(radio)
            if cursor:
                self._close_radios(cursor)

    def _devices(self, radio) -> tuple[ClassicDevice, ...]:
        search = _Search()
        search.size = ctypes.sizeof(search)
        search.authenticated = search.connected = 1
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
                    info.authenticated or info.connected
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
  name=[string]$info.Name;state=[int]$radio.State};
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
    def snapshot(self) -> tuple[Radio, ...]:
        data = _request({"action": "read"})
        entries = data.get("radios")
        if not isinstance(entries, list) or len(entries) > 256:
            raise ValueError("Invalid Bluetooth radios")
        devices = ClassicInventory().snapshot() if entries else {}
        result = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid Bluetooth radio")
            identifier, address = entry.get("id"), entry.get("address")
            name, state = entry.get("name"), entry.get("state")
            if (
                not isinstance(identifier, str)
                or not 0 < len(identifier) <= 32768
                or "\0" in identifier
                or type(address) is not int
                or not 0 < address < 1 << 48
                or not isinstance(name, str)
                or type(state) is not int
                or state not in (0, 1, 2, 3)
            ):
                raise ValueError("Invalid Bluetooth radio properties")
            result[identifier] = Radio(
                identifier, address, name, state, devices.get(address, ())
            )
        return tuple(result.values())

    def set_power(self, radio: Radio, powered: bool) -> bool:
        return (
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
