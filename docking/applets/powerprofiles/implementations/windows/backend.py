"""Windows 11 user power modes, separate AC/DC settings and effective state."""

from __future__ import annotations

import ctypes
import threading
import uuid
from dataclasses import dataclass

from docking.applets.powerprofiles.backend import PowerProfilesControlBackend
from docking.applets.powerprofiles.state import (
    PROFILES,
    PowerProfilesState,
    normalize_profile,
    profile_label,
    unavailable_state,
)
from docking.log import get_logger
from docking.platform.native.windows.com import GUID
from docking.platform.native.windows.ffi import bind
from docking.platform.native.windows.power import SystemPowerStatus

log = get_logger("powerprofiles.windows")
_MODES = {
    "power-saver": "961cc777-2547-4f9d-8174-7d86181b8a7a",
    "balanced": "00000000-0000-0000-0000-000000000000",
    "performance": "ded574b5-45a0-4f42-8737-46345c09c238",
}
_BALANCED_PLAN = "381b4222-f694-41f0-9685-ff5bb260df2e"
# Keep a callback alive if Windows ever rejects its cleanup. Releasing its
# Python trampoline while the OS still owns it would be unsafe.
_retained_registrations: list[object] = []


def _check(code: int, action: str) -> None:
    if code:
        raise OSError(code, action + " failed")


@dataclass(frozen=True)
class _Snapshot:
    source: str
    configured: str
    effective: int | None
    personality: str = _BALANCED_PLAN


class _PowerModes:
    def __init__(self):
        dll = ctypes.WinDLL("powrprof", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._get, self._set = {}, {}
        for source in ("AC", "DC"):
            self._get[source] = bind(
                dll,
                f"PowerGetUserConfigured{source}PowerMode",
                ctypes.c_uint32,
                ctypes.POINTER(GUID),
            )
            self._set[source] = bind(
                dll,
                f"PowerSetUserConfigured{source}PowerMode",
                ctypes.c_uint32,
                ctypes.POINTER(GUID),
            )
        self._status = bind(
            kernel,
            "GetSystemPowerStatus",
            ctypes.c_int32,
            ctypes.POINTER(SystemPowerStatus),
        )
        self._active_plan = bind(
            dll,
            "PowerGetActiveScheme",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.POINTER(GUID)),
        )
        self._free = bind(kernel, "LocalFree", ctypes.c_void_p, ctypes.c_void_p)
        self._plan_value = bind(
            dll,
            "PowerReadACValue",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
            None, ctypes.c_int32, ctypes.c_void_p
        )
        self._register = bind(
            dll,
            "PowerRegisterForEffectivePowerModeNotifications",
            ctypes.c_int32,
            ctypes.c_uint32,
            callback_type,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._unregister = bind(
            dll,
            "PowerUnregisterFromEffectivePowerModeNotifications",
            ctypes.c_int32,
            ctypes.c_void_p,
        )
        self._lock = threading.Lock()
        self._effective: int | None = None
        self._closed = False
        self._registration = ctypes.c_void_p()
        self._callback = callback_type(self._on_effective)
        code = self._register(2, self._callback, None, ctypes.byref(self._registration))
        if code:
            self._registration = ctypes.c_void_p()
            log.debug(
                "Effective power-mode notifications unavailable: %#x", code & 0xFFFFFFFF
            )

    def _on_effective(self, mode: int, _context) -> None:
        # Windows invokes this on its own thread; no GTK or OS calls here.
        with self._lock:
            if not self._closed:
                self._effective = mode

    def source(self) -> str:
        status = SystemPowerStatus()
        if not self._status(ctypes.byref(status)):
            raise OSError(ctypes.get_last_error(), "Power source unavailable")
        if status.ACLineStatus not in (0, 1):
            raise OSError("Power source unknown")
        return "AC" if status.ACLineStatus else "DC"

    def configured(self, source: str) -> str:
        value = GUID()
        _check(self._get[source](ctypes.byref(value)), "Read power mode")
        return str(uuid.UUID(bytes_le=bytes(value)))

    def plan(self) -> tuple[str, str]:
        pointer = ctypes.POINTER(GUID)()
        _check(self._active_plan(None, ctypes.byref(pointer)), "Read active power plan")
        if not pointer:
            raise OSError("Active power plan unavailable")
        try:
            active = GUID.from_buffer_copy(pointer.contents)
        finally:
            self._free(pointer)
        setting = GUID.parse("245d8541-3943-4422-b025-13a784f679b7")
        personality = GUID()
        kind, size = ctypes.c_uint32(), ctypes.c_uint32(ctypes.sizeof(GUID))
        _check(
            self._plan_value(
                None,
                ctypes.byref(active),
                None,
                ctypes.byref(setting),
                ctypes.byref(kind),
                ctypes.byref(personality),
                ctypes.byref(size),
            ),
            "Read power-plan personality",
        )
        if kind.value != 3 or size.value != ctypes.sizeof(GUID):  # REG_BINARY GUID
            raise OSError("Unknown power-plan personality")
        return str(uuid.UUID(bytes_le=bytes(active))), str(
            uuid.UUID(bytes_le=bytes(personality))
        )

    def read(self) -> _Snapshot:
        for _ in range(3):
            active, personality = self.plan()
            source = self.source()
            value = self.configured(source) if personality == _BALANCED_PLAN else ""
            if source == self.source() and active == self.plan()[0]:
                with self._lock:
                    return _Snapshot(source, value, self._effective, personality)
        raise OSError("Power source or plan kept changing")

    def set(self, value: str) -> bool:
        # Resolve the supply at execution time. Never rewrite the other supply's
        # preference or retry against it if the cable changes during the call.
        if self.plan()[1] != _BALANCED_PLAN:
            return False
        source = self.source()
        mode = GUID.parse(value)
        _check(self._set[source](ctypes.byref(mode)), "Set power mode")
        return self.configured(source) == value

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._registration.value:
            # Unregister waits for callbacks, so do not hold their data lock.
            code = self._unregister(self._registration)
            if code:
                _retained_registrations.append(self)
                log.warning(
                    "Could not release power-mode notification: %#x", code & 0xFFFFFFFF
                )
                return
            self._registration = ctypes.c_void_p()
        self._callback = None


def _effective_reason(mode: int | None, configured: str) -> str:
    if mode == 0:
        return "Energy saver active"
    if mode in (5, 6):
        return "Game Mode active" if mode == 5 else "Mixed Reality active"
    effective = {
        1: "power-saver",
        2: "balanced",
        3: "performance",
        4: "performance",
    }.get(mode)
    if effective is None:
        return "Effective power mode unavailable"
    if effective != configured:
        return f"System power mode: {profile_label(effective)}"
    return ""


class WindowsPowerProfilesBackend(PowerProfilesControlBackend):
    def __init__(self, *, api: _PowerModes | None = None):
        self._api = api
        self._lock = threading.RLock()
        self._stopped = False

    def _native(self) -> _PowerModes:
        if self._api is None:
            self._api = _PowerModes()
        return self._api

    def get_state(self) -> PowerProfilesState:
        with self._lock:
            if self._stopped:
                return unavailable_state()
            try:
                snapshot = self._native().read()
                if snapshot.personality != _BALANCED_PLAN:
                    return unavailable_state(
                        error="Select a Balanced power plan in Windows"
                    )
                profile = next(
                    (p for p, g in _MODES.items() if g == snapshot.configured), ""
                )
                if not profile:
                    return unavailable_state(error="Unsupported power mode")
                return PowerProfilesState(
                    available=True,
                    active_profile=profile,
                    profiles=PROFILES,
                    degraded_reason=_effective_reason(snapshot.effective, profile),
                    power_source="Plugged in"
                    if snapshot.source == "AC"
                    else "On battery",
                )
            except (OSError, AttributeError) as exc:
                log.debug("Windows power modes unavailable: %s", exc)
                return unavailable_state(error="Windows power modes unavailable")

    def set_active_profile(self, profile: str) -> bool:
        profile = normalize_profile(profile)
        with self._lock:
            if self._stopped or profile not in _MODES:
                return False
            try:
                return self._native().set(_MODES[profile])
            except (OSError, AttributeError) as exc:
                log.warning("Could not set Windows power mode: %s", exc)
                return False

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            if self._api is not None:
                self._api.stop()
