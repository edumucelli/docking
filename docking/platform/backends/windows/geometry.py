"""Windows monitor coordinates shared by surface placement and screen sampling."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from docking.platform.backends.base import MonitorSnapshot, Rect

from .ffi import MonitorInfo, bind

if TYPE_CHECKING:
    from gi.repository import Gdk


@dataclass(frozen=True)
class ScreenMapping:
    logical: Rect
    physical: Rect
    workarea: Rect
    scale: int

    def to_physical(self, rect: Rect) -> Rect:
        return Rect(
            self.physical.x + (rect.x - self.logical.x) * self.scale,
            self.physical.y + (rect.y - self.logical.y) * self.scale,
            rect.width * self.scale,
            rect.height * self.scale,
        )

    def to_logical(self, rect: Rect) -> Rect:
        relative = Rect(
            rect.x - self.physical.x,
            rect.y - self.physical.y,
            rect.width,
            rect.height,
        ).device_to_logical(self.scale)
        return Rect(
            self.logical.x + relative.x,
            self.logical.y + relative.y,
            relative.width,
            relative.height,
        )


def _rectangle(rect: wintypes.RECT) -> Rect:
    return Rect(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top)


def constrain_window(hwnd: int, workarea: Rect, user: Any = None) -> bool:
    """Fit the actual decorated native frame, without activation or z-order changes.

    GTK's requested content size excludes client-side decorations. Constraining
    the realized frame avoids guessing their size or mixing device/GTK pixels.
    """
    if workarea.width <= 0 or workarea.height <= 0:
        raise OSError("Window work area is unavailable")
    user = user if user is not None else ctypes.WinDLL("user32", use_last_error=True)
    rectangle = wintypes.RECT()
    if not bind(
        user,
        "GetWindowRect",
        wintypes.BOOL,
        wintypes.HWND,
        ctypes.POINTER(wintypes.RECT),
    )(hwnd, ctypes.byref(rectangle)):
        raise ctypes.WinError(ctypes.get_last_error())
    current = _rectangle(rectangle)
    width, height = (
        min(current.width, workarea.width),
        min(current.height, workarea.height),
    )
    x = min(max(current.x, workarea.x), workarea.x + workarea.width - width)
    y = min(max(current.y, workarea.y), workarea.y + workarea.height - height)
    if Rect(x, y, width, height) == current:
        return False
    if not bind(
        user,
        "SetWindowPos",
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    )(hwnd, None, x, y, width, height, 0x14):
        raise ctypes.WinError(ctypes.get_last_error())
    return True


class WindowsMonitorGeometry:
    def __init__(self, user: Any = None) -> None:
        user = (
            user if user is not None else ctypes.WinDLL("user32", use_last_error=True)
        )
        self._monitor_from_point = bind(
            user, "MonitorFromPoint", wintypes.HMONITOR, wintypes.POINT, wintypes.DWORD
        )
        self._monitor_info = bind(
            user,
            "GetMonitorInfoW",
            wintypes.BOOL,
            wintypes.HMONITOR,
            ctypes.POINTER(MonitorInfo),
        )

    def mapping(self, monitor: MonitorSnapshot, display: Gdk.Display) -> ScreenMapping:
        # GDK translates negative Windows origins and scales each monitor. Use
        # the primary origin to locate the native monitor, then map relative to
        # the exact native bounds so mixed scales do not shift the reserved edge.
        primary = display.get_primary_monitor() or display.get_monitor(0)
        origin = primary.get_geometry()
        primary_scale = primary.get_scale_factor()
        target = display.get_monitor(monitor.index) if monitor.index >= 0 else None
        scale = max(
            1, target.get_scale_factor() if target is not None else monitor.scale
        )
        center = wintypes.POINT(
            (monitor.geometry.x + monitor.geometry.width // 2) * scale
            - origin.x * primary_scale,
            (monitor.geometry.y + monitor.geometry.height // 2) * scale
            - origin.y * primary_scale,
        )
        handle = self._monitor_from_point(center, 2)
        info = MonitorInfo(size=ctypes.sizeof(MonitorInfo))
        if not self._monitor_info(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ScreenMapping(
            monitor.geometry, _rectangle(info.monitor), _rectangle(info.workarea), scale
        )

    def point(self, x: int, y: int, display: Gdk.Display) -> tuple[int, int] | None:
        monitor = display.get_monitor_at_point(x, y)
        if monitor is None:
            return None
        bounds = monitor.get_geometry()
        # get_monitor_at_point returns the nearest monitor for desktop gaps.
        if not (
            bounds.x <= x < bounds.x + bounds.width
            and bounds.y <= y < bounds.y + bounds.height
        ):
            return None
        index = next(
            (
                i
                for i in range(display.get_n_monitors())
                if display.get_monitor(i) == monitor
            ),
            -1,
        )
        snapshot = MonitorSnapshot(
            index=index,
            geometry=Rect(bounds.x, bounds.y, bounds.width, bounds.height),
            scale=monitor.get_scale_factor(),
        )
        mapped = self.mapping(snapshot, display).to_physical(Rect(x, y, 1, 1))
        return mapped.x, mapped.y
