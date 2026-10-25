"""Windows monitor brightness through DDC/CI and the WMI panel provider.

All monitor handles are scoped to one request. Device interface identities,
rather than enumeration order, bind queued changes to the primary display.
"""

from __future__ import annotations

import base64
import ctypes
import json
import math
import os
import subprocess
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from docking.applets.brightness.backend import BrightnessBackend
from docking.applets.brightness.state import BrightnessState
from docking.log import get_logger
from docking.platform.backends.windows.ffi import bind

log = get_logger("brightness.windows")
_DWORD = ctypes.c_uint32
_BOOL = ctypes.c_int32
_HANDLE = ctypes.c_void_p


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _Rect(ctypes.Structure):
    _fields_ = [(field, ctypes.c_int32) for field in ("left", "top", "right", "bottom")]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("size", _DWORD),
        ("monitor", _Rect),
        ("work", _Rect),
        ("flags", _DWORD),
        ("device", ctypes.c_wchar * 32),
    ]


class _DisplayDevice(ctypes.Structure):
    _fields_ = [
        ("size", _DWORD),
        ("name", ctypes.c_wchar * 32),
        ("description", ctypes.c_wchar * 128),
        ("flags", _DWORD),
        ("identity", ctypes.c_wchar * 128),
        ("key", ctypes.c_wchar * 128),
    ]


class _PhysicalMonitor(ctypes.Structure):
    _fields_ = [("handle", _HANDLE), ("description", ctypes.c_wchar * 128)]


@dataclass(frozen=True)
class Display:
    handle: int
    name: str
    identity: str


@dataclass(frozen=True)
class MonitorBrightness:
    level: float
    levels: tuple[float, ...] = ()


class MonitorAPI(Protocol):
    def primary(self) -> Display | None: ...
    def ddc(
        self, display: Display, *, value: float | None = None
    ) -> MonitorBrightness | None: ...
    def wmi(
        self, display: Display, *, value: float | None = None
    ) -> MonitorBrightness | None: ...


def _wmi_identity(interface: str) -> str | None:
    # GUID_DEVINTERFACE_MONITOR: \\?\DISPLAY#vendor#instance#{guid}
    parts = interface.removeprefix("\\\\?\\").split("#")
    if len(parts) != 4 or parts[0].casefold() != "display":
        return None
    return "\\".join(parts[:3]).casefold()


_WMI_SCRIPT = r"""
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('REQUEST')) |
 ConvertFrom-Json;
function Read-Panel {
 @(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightness |
   Where-Object {$_.Active -and
    (($_.InstanceName -replace '_\d+$','').ToLowerInvariant() -ceq $request.identity)})
}
$panels=@(Read-Panel);
if($panels.Count -ne 1){'null';exit};
$panel=$panels[0];
$methods=@(Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorBrightnessMethods |
 Where-Object {$_.Active -and $_.InstanceName -eq $panel.InstanceName});
if($methods.Count -ne 1){'null';exit};
if($null -ne $request.percent){
 $levels=@($panel.Level | Where-Object {$_ -ge 10 -and $_ -le 100} |
  Sort-Object @{Expression={[Math]::Abs([int]$_ - $request.percent)}},
   @{Expression={[int]$_}});
 if($levels.Count -eq 0){'null';exit};
 $r=Invoke-CimMethod -InputObject $methods[0] -MethodName WmiSetBrightness `
  -Arguments @{Timeout=[uint32]0;Brightness=[byte]$levels[0]};
 if($r.ReturnValue -ne 0){throw "WmiSetBrightness failed: $($r.ReturnValue)"};
 $panels=@(Read-Panel);
 if($panels.Count -ne 1){'null';exit};
 $panel=$panels[0];
}
@{current=[int]$panel.CurrentBrightness;levels=@($panel.Level)} |
 ConvertTo-Json -Compress
"""


class WindowsMonitorAPI:
    def __init__(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        ddc = ctypes.WinDLL("dxva2", use_last_error=True)
        self._monitor = bind(user, "MonitorFromPoint", _HANDLE, _Point, _DWORD)
        self._info = bind(
            user, "GetMonitorInfoW", _BOOL, _HANDLE, ctypes.POINTER(_MonitorInfo)
        )
        self._devices = bind(
            user,
            "EnumDisplayDevicesW",
            _BOOL,
            ctypes.c_wchar_p,
            _DWORD,
            ctypes.POINTER(_DisplayDevice),
            _DWORD,
        )
        self._count = bind(
            ddc,
            "GetNumberOfPhysicalMonitorsFromHMONITOR",
            _BOOL,
            _HANDLE,
            ctypes.POINTER(_DWORD),
        )
        self._physical = bind(
            ddc,
            "GetPhysicalMonitorsFromHMONITOR",
            _BOOL,
            _HANDLE,
            _DWORD,
            ctypes.POINTER(_PhysicalMonitor),
        )
        self._destroy = bind(
            ddc,
            "DestroyPhysicalMonitors",
            _BOOL,
            _DWORD,
            ctypes.POINTER(_PhysicalMonitor),
        )
        self._caps = bind(
            ddc,
            "GetMonitorCapabilities",
            _BOOL,
            _HANDLE,
            ctypes.POINTER(_DWORD),
            ctypes.POINTER(_DWORD),
        )
        self._get = bind(
            ddc,
            "GetMonitorBrightness",
            _BOOL,
            _HANDLE,
            ctypes.POINTER(_DWORD),
            ctypes.POINTER(_DWORD),
            ctypes.POINTER(_DWORD),
        )
        self._set = bind(ddc, "SetMonitorBrightness", _BOOL, _HANDLE, _DWORD)

    def primary(self) -> Display | None:
        handle = self._monitor(_Point(0, 0), 1)
        info = _MonitorInfo()
        info.size = ctypes.sizeof(info)
        if (
            not handle
            or not self._info(handle, ctypes.byref(info))
            or not info.flags & 1
        ):
            return None
        devices = []
        for index in range(32):
            device = _DisplayDevice()
            device.size = ctypes.sizeof(device)
            if not self._devices(info.device, index, ctypes.byref(device), 1):
                break
            if device.flags & 1 and device.identity:
                devices.append(device.identity.casefold())
        # Cloned displays cannot be safely identified as a single backlight.
        if len(devices) != 1:
            return None
        return Display(handle, info.device, devices[0])

    def ddc(
        self, display: Display, *, value: float | None = None
    ) -> MonitorBrightness | None:
        if self.primary() != display:
            return None
        count = _DWORD()
        if not self._count(display.handle, ctypes.byref(count)) or count.value != 1:
            return None
        monitors = (_PhysicalMonitor * count.value)()
        if not self._physical(display.handle, count, monitors):
            return None
        try:
            handle = monitors[0].handle
            caps, temperatures = _DWORD(), _DWORD()
            if (
                not self._caps(handle, ctypes.byref(caps), ctypes.byref(temperatures))
                or not caps.value & 2
            ):
                return None
            minimum, current, maximum = _DWORD(), _DWORD(), _DWORD()
            if not self._get(
                handle,
                ctypes.byref(minimum),
                ctypes.byref(current),
                ctypes.byref(maximum),
            ):
                return None
            if (
                not minimum.value <= current.value <= maximum.value
                or maximum.value <= minimum.value
            ):
                return None
            if value is not None:
                if self.primary() != display:
                    return None
                requested = minimum.value + round(
                    (maximum.value - minimum.value) * max(0.1, min(1.0, value))
                )
                # Read back even when the monitor denies or rounds the write.
                self._set(handle, requested)
                if not self._get(
                    handle,
                    ctypes.byref(minimum),
                    ctypes.byref(current),
                    ctypes.byref(maximum),
                ):
                    return None
                if (
                    not minimum.value <= current.value <= maximum.value
                    or maximum.value <= minimum.value
                ):
                    return None
            steps = maximum.value - minimum.value
            levels = (
                tuple(
                    index / steps for index in range(math.ceil(steps * 0.1), steps + 1)
                )
                if steps <= 1000
                else ()
            )
            return MonitorBrightness((current.value - minimum.value) / steps, levels)
        finally:
            if not self._destroy(count, monitors):
                log.debug("DestroyPhysicalMonitors failed: %s", ctypes.get_last_error())

    def wmi(
        self, display: Display, *, value: float | None = None
    ) -> MonitorBrightness | None:
        identity = _wmi_identity(display.identity)
        if identity is None or self.primary() != display:
            return None
        payload = base64.b64encode(
            json.dumps(
                {
                    "identity": identity,
                    "percent": None
                    if value is None
                    else round(max(0.1, min(1.0, value)) * 100),
                }
            ).encode()
        ).decode()
        script = _WMI_SCRIPT.replace("REQUEST", payload)
        # Bound provider hangs and keep PowerShell off the desktop. Both the
        # script and its input use encoded arguments, with no shell expansion.
        executable = (
            Path(os.environ.get("SYSTEMROOT", "C:\\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        try:
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
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if not isinstance(data, dict):
                    return None
                percent, raw_levels = data.get("current"), data.get("levels")
                if (
                    type(percent) is int
                    and 0 <= percent <= 100
                    and isinstance(raw_levels, list)
                ):
                    levels = tuple(
                        sorted(
                            {
                                level / 100
                                for level in raw_levels
                                if type(level) is int and 10 <= level <= 100
                            }
                        )
                    )
                    if levels:
                        return MonitorBrightness(percent / 100, levels)
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            log.debug("Panel brightness unavailable: %s", exc)
        return None


@lru_cache(maxsize=1)
def _native_api() -> WindowsMonitorAPI:
    return WindowsMonitorAPI()


class WindowsBrightnessBackend(BrightnessBackend):
    def __init__(self, *, api: MonitorAPI | None = None) -> None:
        self._api = api
        self._lock = threading.Lock()
        self._stopped = threading.Event()

    @property
    def api(self) -> MonitorAPI:
        return self._api if self._api is not None else _native_api()

    def _read(self) -> tuple[Display | None, BrightnessState]:
        if self._stopped.is_set():
            return None, BrightnessState()
        display = self.api.primary()
        if display is not None:
            for provider in ("ddc", "wmi"):
                if self._stopped.is_set():
                    break
                reading = getattr(self.api, provider)(display)
                if (
                    reading is not None
                    and math.isfinite(reading.level)
                    and 0 <= reading.level <= 1
                ):
                    if self.api.primary() == display and not self._stopped.is_set():
                        return display, BrightnessState(
                            reading.level,
                            f"{provider}:{display.identity}",
                            reading.levels,
                        )
                    break
        return None, BrightnessState()

    def get_state(self) -> BrightnessState:
        with self._lock:
            return self._read()[1]

    def set_brightness(self, *, value: float, target: str) -> BrightnessState:
        with self._lock:
            display, state = self._read()
            if display is None or state.target != target or not math.isfinite(value):
                return state
            if not self._stopped.is_set():
                provider = target.split(":", 1)[0]
                getattr(self.api, provider)(display, value=max(0.1, min(1.0, value)))
            return self._read()[1]

    def stop(self) -> None:
        self._stopped.set()
