"""Native Windows aggregate battery status and instantaneous power."""

from __future__ import annotations

import ctypes
import subprocess
from functools import lru_cache
from typing import Protocol

from docking.applets.battery.backend import BatteryBackend
from docking.applets.battery.implementations.windows.peripherals import (
    read_peripheral_batteries,
)
from docking.applets.battery.peripherals import PeripheralBattery
from docking.applets.battery.state import BatteryState, resolve_battery_icon
from docking.log import get_logger
from docking.platform.native.windows.ffi import bind as _bind
from docking.platform.native.windows.power import SystemPowerStatus

log = get_logger("battery.windows")
_UNKNOWN_TIME = 0xFFFFFFFF
_UNKNOWN_RATE = -0x80000000


class SystemBatteryState(ctypes.Structure):
    _fields_ = [
        ("AcOnLine", ctypes.c_ubyte),
        ("BatteryPresent", ctypes.c_ubyte),
        ("Charging", ctypes.c_ubyte),
        ("Discharging", ctypes.c_ubyte),
        ("Spare1", ctypes.c_ubyte * 3),
        ("Tag", ctypes.c_ubyte),
        ("MaxCapacity", ctypes.c_uint32),
        ("RemainingCapacity", ctypes.c_uint32),
        # The SDK declares DWORD, but explicitly requires signed LONG semantics.
        ("Rate", ctypes.c_int32),
        ("EstimatedTime", ctypes.c_uint32),
        ("DefaultAlert1", ctypes.c_uint32),
        ("DefaultAlert2", ctypes.c_uint32),
    ]


class PowerAPI(Protocol):
    def power_status(self) -> SystemPowerStatus | None: ...
    def battery_state(self) -> SystemBatteryState | None: ...
    def peripheral_batteries(self) -> list[PeripheralBattery]: ...


class WindowsPowerAPI:
    def __init__(self) -> None:
        self._status = _bind(
            ctypes.WinDLL("kernel32", use_last_error=True),
            "GetSystemPowerStatus",
            ctypes.c_int32,
            ctypes.POINTER(SystemPowerStatus),
        )
        self._battery = _bind(
            ctypes.WinDLL("powrprof"),
            "CallNtPowerInformation",
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )

    def power_status(self) -> SystemPowerStatus | None:
        value = SystemPowerStatus()
        if not self._status(ctypes.byref(value)):
            log.debug("GetSystemPowerStatus failed: %s", ctypes.get_last_error())
            return None
        return value

    def battery_state(self) -> SystemBatteryState | None:
        value = SystemBatteryState()
        status = self._battery(5, None, 0, ctypes.byref(value), ctypes.sizeof(value))
        if status != 0:
            log.debug(
                "SystemBatteryState unavailable: NTSTATUS %#x", status & 0xFFFFFFFF
            )
            return None
        return value

    def peripheral_batteries(self) -> list[PeripheralBattery]:
        return read_peripheral_batteries()


@lru_cache(maxsize=1)
def _native_api() -> WindowsPowerAPI:
    # Cache DLL bindings only; each poll reads fresh power information.
    return WindowsPowerAPI()


class WindowsBatteryBackend(BatteryBackend):
    def __init__(self, *, api: PowerAPI | None = None) -> None:
        self._api = api

    def read_battery(self) -> BatteryState | None:
        api = self._api if self._api is not None else _native_api()
        reading = api.power_status()
        if reading is None:
            return BatteryState("battery-empty", None, "Unknown", None)
        flags = reading.BatteryFlag
        # 255 is unknown, not the OR of all defined flags (including no battery).
        if flags != 255 and flags & 128:
            return None
        capacity = reading.BatteryLifePercent
        if capacity > 100:
            capacity = None
        status = "Unknown"
        if flags != 255:
            if flags & 8:
                status = "Charging"
            elif reading.ACLineStatus == 0:
                status = "Discharging"
            elif reading.ACLineStatus == 1:
                status = "Full" if capacity == 100 else "Not charging"
        remaining = reading.BatteryLifeTime
        seconds = (
            remaining
            if status == "Discharging" and 0 < remaining < _UNKNOWN_TIME
            else None
        )
        level = (
            "unknown"
            if capacity is None
            else "full"
            if capacity >= 95
            else "high"
            if capacity >= 60
            else "normal"
            if capacity >= 30
            else "low"
            if capacity >= 10
            else "critical"
        )
        return BatteryState(
            resolve_battery_icon(level, status),
            capacity,
            status,
            seconds,
            self.read_power_watts(),
        )

    def read_power_watts(self) -> float | None:
        api = self._api if self._api is not None else _native_api()
        reading = api.battery_state()
        if (
            reading is None
            or not reading.BatteryPresent
            or reading.Rate == _UNKNOWN_RATE
        ):
            return None
        return abs(int(reading.Rate)) / 1000.0

    def read_peripherals(self) -> list[PeripheralBattery]:
        api = self._api if self._api is not None else _native_api()
        try:
            return api.peripheral_batteries()
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            log.debug("Windows peripheral battery query unavailable: %s", exc)
            return []

    def settings_available(self) -> bool:
        return True

    def open_settings(self) -> bool:
        from docking.platform.native.windows.shell import open_default_target

        return open_default_target("ms-settings:powersleep")
