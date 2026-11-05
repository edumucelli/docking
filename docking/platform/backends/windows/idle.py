"""Session-specific Windows idle time behind the common IdleService contract."""

from __future__ import annotations

import ctypes
from typing import Protocol

from docking.log import get_logger
from docking.platform.backends.base import IdleService
from docking.platform.native.windows.ffi import bind

log = get_logger("windows.idle")


class LastInputInfo(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("tick", ctypes.c_uint32)]


class InputAPI(Protocol):
    def sample(self) -> tuple[int, int]: ...


class WindowsInputAPI:
    def __init__(self) -> None:
        self._last = bind(
            ctypes.WinDLL("user32", use_last_error=True),
            "GetLastInputInfo",
            ctypes.c_int32,
            ctypes.POINTER(LastInputInfo),
        )
        self._ticks = bind(
            ctypes.WinDLL("kernel32", use_last_error=True),
            "GetTickCount64",
            ctypes.c_uint64,
        )

    def sample(self) -> tuple[int, int]:
        info = LastInputInfo(size=ctypes.sizeof(LastInputInfo))
        if not self._last(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(info.tick), int(self._ticks())


def idle_interval(last_input: int, uptime: int) -> float | None:
    if not 0 <= last_input <= 0xFFFFFFFF or not 0 <= uptime <= 0xFFFFFFFFFFFFFFFF:
        return None
    elapsed = (uptime - last_input) & 0xFFFFFFFF
    # SendInput can supply its own timestamp. A duration older than the
    # entire system uptime cannot establish session idleness.
    if elapsed > uptime:
        return None
    return elapsed / 1000.0


class WindowsIdleService(IdleService):
    def __init__(self, *, api: InputAPI | None = None) -> None:
        self._api = api
        self._stopped = False
        self._generation = 0

    def start(self) -> None:
        self._generation += 1
        self._stopped = False

    def stop(self) -> None:
        self._generation += 1
        self._stopped = True

    def idle_seconds(self) -> float | None:
        if self._stopped:
            return None
        generation = self._generation
        try:
            if self._api is None:
                self._api = WindowsInputAPI()
            last_input, uptime = self._api.sample()
        except OSError as exc:
            log.debug("Session idle query failed: %s", exc)
            return None
        if self._stopped or generation != self._generation:
            return None
        return idle_interval(last_input, uptime)
