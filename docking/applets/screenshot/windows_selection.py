"""Preserve device-pixel mouse positions before GDK Win32 rounds them."""

from __future__ import annotations

import ctypes
from collections import deque
from ctypes import wintypes

from docking.log import get_logger
from docking.platform.backends.base import MonitorSnapshot, Rect
from docking.platform.backends.windows.ffi import bind, window_handle
from docking.platform.backends.windows.geometry import WindowsMonitorGeometry

from .backend import SelectionPhase

log = get_logger("screenshot.windows_selection")
_PHASES: dict[int, SelectionPhase] = {0x201: "press", 0x200: "motion", 0x202: "release"}


class WindowsRegionCoordinates:
    """Observe only the owned overlay's input, without consuming its messages."""

    def __init__(self, window: object) -> None:
        self._hwnd = None
        self._positions: dict[SelectionPhase, deque[tuple[int, int]]] = {
            "press": deque(maxlen=32),
            "motion": deque(maxlen=1),
            "release": deque(maxlen=32),
        }
        display = window.get_display()
        geometry = WindowsMonitorGeometry()
        self._mappings = []
        for index in range(display.get_n_monitors()):
            monitor = display.get_monitor(index)
            bounds = monitor.get_geometry()
            self._mappings.append(
                geometry.mapping(
                    MonitorSnapshot(
                        index=index,
                        geometry=Rect(bounds.x, bounds.y, bounds.width, bounds.height),
                        scale=monitor.get_scale_factor(),
                    ),
                    display,
                )
            )
        user = ctypes.WinDLL("user32", use_last_error=True)
        self._to_screen = bind(
            user,
            "ClientToScreen",
            wintypes.BOOL,
            wintypes.HWND,
            ctypes.POINTER(wintypes.POINT),
        )
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            ctypes.c_size_t,
            ctypes.c_size_t,
        )
        self._callback = callback_type(self._message)
        library = ctypes.WinDLL("comctl32", use_last_error=True)
        self._set = bind(
            library,
            "SetWindowSubclass",
            wintypes.BOOL,
            wintypes.HWND,
            callback_type,
            ctypes.c_size_t,
            ctypes.c_size_t,
        )
        self._remove = bind(
            library,
            "RemoveWindowSubclass",
            wintypes.BOOL,
            wintypes.HWND,
            callback_type,
            ctypes.c_size_t,
        )
        self._next = bind(
            library,
            "DefSubclassProc",
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        hwnd = window_handle(window.get_window())
        if hwnd is None:
            raise OSError("Region overlay has no native window")
        if not self._set(hwnd, self._callback, id(self), 0):
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd

    def _message(self, hwnd, message, wparam, lparam, _subclass, _data):
        try:
            phase = _PHASES.get(message)
            if phase is not None:
                point = wintypes.POINT(
                    ctypes.c_int16(lparam & 0xFFFF).value,
                    ctypes.c_int16((lparam >> 16) & 0xFFFF).value,
                )
                if self._to_screen(hwnd, ctypes.byref(point)):
                    self._positions[phase].append((point.x, point.y))
            elif message == 0x82:  # WM_NCDESTROY
                self.close()
        except Exception:
            log.exception("Region input observer failed")
        return self._next(hwnd, message, wparam, lparam)

    def point(self, phase: SelectionPhase) -> tuple[float, float] | None:
        positions = self._positions.get(phase)
        if not positions:
            return None
        x, y = positions.popleft()
        for mapping in self._mappings:
            bounds = mapping.physical
            if bounds.x <= x < bounds.right and bounds.y <= y < bounds.bottom:
                return (
                    mapping.logical.x + (x - bounds.x) / mapping.scale,
                    mapping.logical.y + (y - bounds.y) / mapping.scale,
                )
        return None

    def close(self) -> None:
        if self._hwnd is not None:
            self._remove(self._hwnd, self._callback, id(self))
            self._hwnd = None
        self._positions.clear()
