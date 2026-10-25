"""Windows AppBar reservation and physical/logical monitor coordinates.

A transparent, non-interactive native window reserves the resting shelf strip.
The GTK window remains larger to accommodate icon magnification and animation.
The proxy owns its callback and is removed from the shell before destruction.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docking.core.position import Position
from docking.log import get_logger
from docking.platform.backends.base import MonitorSnapshot, Rect

from .ffi import MonitorInfo
from .ffi import bind as _bind

if TYPE_CHECKING:
    from gi.repository import Gdk

log = get_logger("windows.appbar")


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


def edge_strip(bounds: Rect, position: Position, thickness: int) -> Rect:
    """Size a shell-approved rectangle along its requested screen edge."""
    if position in (Position.TOP, Position.BOTTOM):
        height = min(bounds.height, max(0, thickness))
        y = bounds.bottom - height if position is Position.BOTTOM else bounds.y
        return Rect(bounds.x, y, bounds.width, height)
    width = min(bounds.width, max(0, thickness))
    x = bounds.right - width if position is Position.RIGHT else bounds.x
    return Rect(x, bounds.y, width, bounds.height)


class _AppBarData(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("edge", wintypes.UINT),
        ("rect", wintypes.RECT),
        ("parameter", wintypes.LPARAM),
    ]


def _rectangle(rect: wintypes.RECT) -> Rect:
    return Rect(
        int(rect.left),
        int(rect.top),
        int(rect.right - rect.left),
        int(rect.bottom - rect.top),
    )


class WindowsAppBar:
    def __init__(
        self, on_change: Callable[[], None], on_fullscreen: Callable[[bool], None]
    ) -> None:
        self._on_change = on_change
        self._on_fullscreen = on_fullscreen
        self._registered = False
        self._reserved: Rect | None = None
        self._edge: int | None = None
        self._closed = False
        self._user = user = ctypes.WinDLL("user32", use_last_error=True)
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._appbar = _bind(
            shell,
            "SHAppBarMessage",
            ctypes.c_size_t,
            wintypes.DWORD,
            ctypes.POINTER(_AppBarData),
        )
        self._cursor = _bind(
            user, "GetCursorPos", wintypes.BOOL, ctypes.POINTER(wintypes.POINT)
        )
        self._monitor_from_point = _bind(
            user, "MonitorFromPoint", wintypes.HMONITOR, wintypes.POINT, wintypes.DWORD
        )
        self._monitor_info = _bind(
            user,
            "GetMonitorInfoW",
            wintypes.BOOL,
            wintypes.HMONITOR,
            ctypes.POINTER(MonitorInfo),
        )
        register_message = _bind(
            user, "RegisterWindowMessageW", wintypes.UINT, wintypes.LPCWSTR
        )
        self._message = register_message("Docking.AppBar.Notify")
        self._taskbar_created = register_message("TaskbarCreated")
        self._default = _bind(
            user,
            "DefWindowProcW",
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._set_position = _bind(
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
        )
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

        class WindowClass(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.UINT),
                ("style", wintypes.UINT),
                ("callback", callback_type),
                ("class_extra", ctypes.c_int),
                ("window_extra", ctypes.c_int),
                ("instance", wintypes.HINSTANCE),
                ("icon", wintypes.HICON),
                ("cursor", wintypes.HANDLE),
                ("background", wintypes.HBRUSH),
                ("menu", wintypes.LPCWSTR),
                ("name", wintypes.LPCWSTR),
                ("small_icon", wintypes.HICON),
            ]

        self._callback = callback_type(self._window_proc)
        self._instance = _bind(
            kernel, "GetModuleHandleW", wintypes.HMODULE, wintypes.LPCWSTR
        )(None)
        self._class_name = f"Docking.AppBar.{os.getpid()}.{id(self)}"
        window_class = WindowClass(
            size=ctypes.sizeof(WindowClass),
            callback=self._callback,
            instance=self._instance,
            name=self._class_name,
        )
        register = _bind(
            user, "RegisterClassExW", wintypes.ATOM, ctypes.POINTER(WindowClass)
        )
        if not register(ctypes.byref(window_class)):
            raise ctypes.WinError(ctypes.get_last_error())
        create = _bind(
            user,
            "CreateWindowExW",
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        )
        # LAYERED | TRANSPARENT | NOACTIVATE | TOOLWINDOW, with no decorations.
        self._hwnd = create(
            0x080800A0,
            self._class_name,
            "Docking reservation",
            0x80000000,
            0,
            0,
            0,
            0,
            None,
            None,
            self._instance,
            None,
        )
        if not self._hwnd:
            self._unregister_class()
            raise ctypes.WinError(ctypes.get_last_error())
        opacity = _bind(
            user,
            "SetLayeredWindowAttributes",
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.BYTE,
            wintypes.DWORD,
        )
        opacity(self._hwnd, 0, 0, 2)

    def _data(self, edge: int = 0, rect: Rect | None = None) -> _AppBarData:
        data = _AppBarData(
            size=ctypes.sizeof(_AppBarData),
            hwnd=self._hwnd,
            message=self._message,
            edge=edge,
        )
        if rect is not None:
            data.rect = wintypes.RECT(rect.x, rect.y, rect.right, rect.bottom)
        return data

    def cursor_position(self) -> tuple[int, int] | None:
        point = wintypes.POINT()
        return (point.x, point.y) if self._cursor(ctypes.byref(point)) else None

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

    @staticmethod
    def _edge_for(position: Position) -> int:
        return {
            Position.LEFT: 0,
            Position.TOP: 1,
            Position.RIGHT: 2,
            Position.BOTTOM: 3,
        }[position]

    def external(self, mapping: ScreenMapping, position: Position) -> Rect:
        if not self._registered:
            return mapping.workarea
        data = self._data(self._edge_for(position), mapping.physical)
        self._appbar(2, ctypes.byref(data))  # QUERYPOS excludes our own AppBar.
        return _rectangle(data.rect)

    def reserve(
        self, mapping: ScreenMapping, position: Position, thickness: int
    ) -> None:
        if not self._registered:
            data = self._data()
            if not self._appbar(0, ctypes.byref(data)):
                raise OSError("Windows shell rejected AppBar registration")
            self._registered = True
        edge = self._edge_for(position)
        bounds = self.external(mapping, position)
        rect = edge_strip(bounds, position, thickness * mapping.scale)
        if rect == self._reserved and edge == self._edge:
            return
        data = self._data(edge, rect)
        self._appbar(3, ctypes.byref(data))
        rect = _rectangle(data.rect)
        self._reserved, self._edge = rect, edge
        # NOACTIVATE | SHOWWINDOW. The transparent proxy is never interactive.
        self._set_position(
            self._hwnd, None, rect.x, rect.y, rect.width, rect.height, 0x50
        )

    def clear(self) -> None:
        if self._registered:
            self._registered = False
            self._reserved = None
            self._edge = None
            self._appbar(1, ctypes.byref(self._data()))
            _bind(self._user, "ShowWindow", wintypes.BOOL, wintypes.HWND, ctypes.c_int)(
                self._hwnd, 0
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.clear()
        _bind(self._user, "DestroyWindow", wintypes.BOOL, wintypes.HWND)(self._hwnd)
        self._unregister_class()

    def _unregister_class(self) -> None:
        _bind(
            self._user,
            "UnregisterClassW",
            wintypes.BOOL,
            wintypes.LPCWSTR,
            wintypes.HINSTANCE,
        )(self._class_name, self._instance)

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        try:
            if not self._closed:
                if message == self._message and wparam == 1:  # ABN_POSCHANGED
                    self._on_change()
                    return 0
                if message == self._message and wparam == 2:  # ABN_FULLSCREENAPP
                    self._on_fullscreen(bool(lparam))
                    return 0
                if message == self._taskbar_created:
                    self._registered = False
                    self._reserved = None
                    self._on_change()
                elif self._registered and message in (6, 0x47):
                    data = self._data()
                    data.parameter = bool(wparam & 0xFFFF) if message == 6 else 0
                    self._appbar(6 if message == 6 else 9, ctypes.byref(data))
                elif message in (0x1A, 0x7E):
                    self._on_change()
        except Exception:
            log.exception("Windows AppBar callback failed")
        return int(self._default(hwnd, message, wparam, lparam))
