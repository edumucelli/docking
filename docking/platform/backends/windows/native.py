"""Win32 window enumeration and actions, with pointer-sized ctypes bindings.

DLLs are loaded only when the native backend starts. Packaged applications use
their Windows application user model ID; ordinary applications use executable
identity. A snapshot never keeps a process handle open.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import PureWindowsPath
from typing import Any

from docking.log import get_logger
from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    Rect,
    WindowId,
    WindowSnapshot,
)

log = get_logger("windows.native")


def _bind(library: Any, name: str, result: Any, *arguments: Any) -> Any:
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


class WindowsNativeAPI:
    """Own the small native API boundary used by window tracking."""

    def __init__(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
        self._callback_type = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        self._enumerate = _bind(
            user, "EnumWindows", wintypes.BOOL, self._callback_type, wintypes.LPARAM
        )
        self._foreground = _bind(user, "GetForegroundWindow", wintypes.HWND)
        self._visible = _bind(user, "IsWindowVisible", wintypes.BOOL, wintypes.HWND)
        self._exists = _bind(user, "IsWindow", wintypes.BOOL, wintypes.HWND)
        self._iconic = _bind(user, "IsIconic", wintypes.BOOL, wintypes.HWND)
        self._zoomed = _bind(user, "IsZoomed", wintypes.BOOL, wintypes.HWND)
        self._owner = _bind(
            user, "GetWindow", wintypes.HWND, wintypes.HWND, wintypes.UINT
        )
        self._style = _bind(
            user, "GetWindowLongPtrW", ctypes.c_ssize_t, wintypes.HWND, ctypes.c_int
        )
        self._pid = _bind(
            user,
            "GetWindowThreadProcessId",
            wintypes.DWORD,
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._title = _bind(
            user,
            "GetWindowTextW",
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        self._class = _bind(
            user,
            "GetClassNameW",
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        self._rect = _bind(
            user,
            "GetWindowRect",
            wintypes.BOOL,
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        self._dwm_attribute = _bind(
            dwm,
            "DwmGetWindowAttribute",
            wintypes.LONG,
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        self._open_process = _bind(
            kernel,
            "OpenProcess",
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        self._close_handle = _bind(
            kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE
        )
        self._executable = _bind(
            kernel,
            "QueryFullProcessImageNameW",
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._aumid = _bind(
            kernel,
            "GetApplicationUserModelId",
            wintypes.LONG,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.UINT),
            wintypes.LPWSTR,
        )
        self._show = _bind(
            user, "ShowWindowAsync", wintypes.BOOL, wintypes.HWND, ctypes.c_int
        )
        self._activate = _bind(
            user, "SetForegroundWindow", wintypes.BOOL, wintypes.HWND
        )
        self._post = _bind(
            user,
            "PostMessageW",
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

    def _process_identity(self, pid: int) -> tuple[str | None, str]:
        process = self._open_process(0x1000, False, pid)  # query limited information
        if not process:
            return None, ""
        try:
            application_id = None
            size = wintypes.UINT()
            status = self._aumid(process, ctypes.byref(size), None)
            if status == 122 and 0 < size.value <= 4096:  # insufficient buffer
                buffer = ctypes.create_unicode_buffer(size.value)
                if self._aumid(process, ctypes.byref(size), buffer) == 0:
                    application_id = str(buffer.value)
            size = wintypes.DWORD(32768)
            executable = ctypes.create_unicode_buffer(size.value)
            path = (
                executable.value
                if self._executable(process, 0, executable, ctypes.byref(size))
                else ""
            )
            return application_id, PureWindowsPath(path).stem.lower() if path else ""
        finally:
            self._close_handle(process)

    def windows(self) -> tuple[WindowSnapshot, ...]:
        foreground = self._foreground()
        rows: list[WindowSnapshot] = []
        identities: dict[int, tuple[str | None, str]] = {}

        def visit(hwnd: int, _data: int) -> bool:
            try:
                row = self._snapshot(hwnd, foreground, identities)
                if row is not None:
                    rows.append(row)
            except Exception:
                # Windows may disappear or deny access between native calls;
                # exceptions must never escape a ctypes callback.
                log.exception("Could not inspect native window %s", hwnd)
            return True

        callback = self._callback_type(visit)
        if not self._enumerate(callback, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return tuple(rows)

    def _snapshot(
        self,
        hwnd: int,
        foreground: int | None,
        identities: dict[int, tuple[str | None, str]],
    ) -> WindowSnapshot | None:
        if not self._visible(hwnd):
            return None
        pid = wintypes.DWORD()
        self._pid(hwnd, ctypes.byref(pid))
        if not pid.value or pid.value == os.getpid():
            return None
        extended_style = self._style(hwnd, -20)
        # Explorer's normal taskbar candidates are unowned application windows.
        # Exclude tool palettes and passive shell surfaces unless explicitly
        # marked APPWINDOW. Minimized windows remain IsWindowVisible.
        app_window = bool(extended_style & 0x00040000)
        if not app_window and (
            extended_style & (0x00000080 | 0x08000000) or self._owner(hwnd, 4)
        ):
            return None
        class_name = ctypes.create_unicode_buffer(256)
        self._class(hwnd, class_name, len(class_name))
        if class_name.value in {
            "Progman",
            "WorkerW",
            "Shell_TrayWnd",
            "Shell_SecondaryTrayWnd",
        }:
            return None
        cloaked = wintypes.DWORD()
        if (
            self._dwm_attribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            == 0
            and cloaked.value
        ):
            return None
        title = ctypes.create_unicode_buffer(1024)
        self._title(hwnd, title, len(title))
        if not title.value:
            return None
        if pid.value not in identities:
            identities[pid.value] = self._process_identity(pid.value)
        app_id, executable = identities[pid.value]
        rectangle = wintypes.RECT()
        geometry = None
        if self._rect(hwnd, ctypes.byref(rectangle)):
            geometry = Rect(
                rectangle.left,
                rectangle.top,
                max(0, rectangle.right - rectangle.left),
                max(0, rectangle.bottom - rectangle.top),
            )
        return WindowSnapshot(
            id=WindowId(DisplayServer.WINDOWS, hwnd),
            desktop_id="",
            title=title.value,
            app_id=app_id or executable or class_name.value,
            wm_class=class_name.value,
            pid=pid.value,
            active=hwnd == foreground,
            visible=True,
            minimized=bool(self._iconic(hwnd)),
            maximized=bool(self._zoomed(hwnd)),
            geometry=geometry,
            can_activate=True,
            can_minimize=True,
            can_close=True,
        )

    def perform(self, window: WindowSnapshot, action: str) -> ActionResult:
        if window.id.backend is not DisplayServer.WINDOWS:
            return ActionResult.NOT_FOUND
        hwnd = int(window.id.value)
        if not self._exists(hwnd):
            return ActionResult.NOT_FOUND
        pid = wintypes.DWORD()
        self._pid(hwnd, ctypes.byref(pid))
        if pid.value != window.pid:
            return ActionResult.NOT_FOUND
        if action == "close":
            accepted = self._post(hwnd, 0x0010, 0, 0)
        elif action == "minimize":
            accepted = self._show(hwnd, 6)
        elif action == "activate":
            if self._iconic(hwnd):
                self._show(hwnd, 9)
            accepted = self._activate(hwnd)
        else:
            return ActionResult.UNSUPPORTED
        return ActionResult.OK if accepted else ActionResult.FAILED
