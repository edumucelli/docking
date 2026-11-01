"""Complete a user-requested Search activation across Windows shell handoff.

The Start menu can return foreground ownership to Explorer after GTK has
already focused its own window. Observe that handoff, then finish activation
on GTK's idle loop. Another application taking focus cancels the request.
"""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes
from pathlib import PureWindowsPath

from gi.repository import GLib

from docking.platform.desktop.implementations.windows.menus import WindowsMenuFocus
from docking.platform.native.windows.ffi import bind as _bind
from docking.search.services.window_focus import WindowFocus

_EXPLORER_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
_SHELL_PROCESSES = {
    "startmenuexperiencehost",
    "searchhost",
    "searchapp",
    "shellexperiencehost",
}


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("tick", ctypes.c_uint32)]


class WindowsShellFocus(WindowsMenuFocus):
    def __init__(self):
        super().__init__()
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._last_input = _bind(
            user, "GetLastInputInfo", wintypes.BOOL, ctypes.POINTER(_LastInputInfo)
        )
        self._class = _bind(
            user,
            "GetClassNameW",
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        self._title = _bind(
            user,
            "GetWindowTextW",
            ctypes.c_int,
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        self._open = _bind(
            kernel,
            "OpenProcess",
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
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
        self._close = _bind(kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)

    def input_time(self) -> int | None:
        info = _LastInputInfo(size=ctypes.sizeof(_LastInputInfo))
        return int(info.tick) if self._last_input(ctypes.byref(info)) else None

    def _name(self, function, hwnd):
        value = ctypes.create_unicode_buffer(512)
        function(hwnd, value, len(value))
        return value.value

    def _process(self, pid):
        handle = self._open(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            value = ctypes.create_unicode_buffer(size.value)
            if self._executable(handle, 0, value, ctypes.byref(size)):
                return PureWindowsPath(value.value).stem.casefold()
            return ""
        finally:
            self._close(handle)

    def shell_return(self, identity):
        return bool(
            identity
            and self._name(self._class, identity[0]) in _EXPLORER_CLASSES
            and self._process(identity[1]) == "explorer"
        )

    def shell_origin(self, identity):
        return self.shell_return(identity) or bool(
            identity
            and self._name(self._class, identity[0]) == "Windows.UI.Core.CoreWindow"
            and self._process(identity[1]) in _SHELL_PROCESSES
        )

    def target(self, title):
        identity = self.active()
        if (
            identity
            and identity[1] == os.getpid()
            and self._name(self._title, identity[0]) == title
        ):
            return identity
        return None


class WindowsSearchFocus(WindowFocus):
    def __init__(self, *, native=None):
        self._native = native if native is not None else WindowsShellFocus()
        self._pending = False
        self._origin = None
        self._target = None
        self._acquired_focus = False
        self._input_time = None
        self._visible = lambda: False
        self._idle = 0
        self._expiry = 0

    def before_present(self) -> None:
        self.cancel()
        self._origin = self._native.foreground()
        if self._native.shell_origin(self._origin):
            self._pending = True
            self._input_time = self._native.input_time()
            self._native.start(self._changed)
            self._expiry = GLib.timeout_add(750, self._expire)

    def after_present(self, *, title: str, visible: Callable[[], bool]) -> None:
        if not self._pending:
            return
        self._target = self._native.target(title)
        self._visible = visible
        if self._target is None:
            self.cancel()
        else:
            self._changed(None)

    def _changed(self, _identity):
        if not self._pending or self._target is None:
            return
        current = self._native.foreground()
        if current == self._target:
            if not self._acquired_focus:
                self._acquired_focus = True
                self._input_time = self._native.input_time()
        elif self._eligible_shell(current):
            if not self._input_unchanged():
                self.cancel()
            elif not self._idle:
                self._idle = GLib.idle_add(self._finish)
        elif current is not None and current != self._origin:
            self.cancel()

    def _finish(self):
        self._idle = 0
        if not self._pending or not self._visible():
            self.cancel()
            return False
        current = self._native.foreground()
        if self._eligible_shell(current) and self._input_unchanged():
            self._native.restore(self._target, current)
        elif current != self._target:
            self.cancel()
        return False

    def _eligible_shell(self, identity):
        return self._native.shell_return(identity) or (
            self._acquired_focus and identity == self._origin
        )

    def _input_unchanged(self):
        return (
            self._input_time is not None
            and self._native.input_time() == self._input_time
        )

    def _expire(self):
        self._expiry = 0
        self.cancel()
        return False

    def cancel(self) -> None:
        self._pending = False
        self._native.stop()
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0
        if self._expiry:
            GLib.source_remove(self._expiry)
            self._expiry = 0
        self._origin = self._target = None
        self._acquired_focus = False
        self._input_time = None
        self._visible = lambda: False
