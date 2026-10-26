"""Small Win32 ABI primitives shared by Windows service implementations."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Any


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("monitor", wintypes.RECT),
        ("workarea", wintypes.RECT),
        ("flags", wintypes.DWORD),
    ]


def bind(library: Any, name: str, result: Any, *arguments: Any) -> Any:
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


def window_handle(window: object) -> int | None:
    """Resolve a live GdkWindow through GDK's exported Win32 ABI.

    GTK 3 does not introspect this function. PyGObject's capsule exposes the
    borrowed GObject pointer; keep its owner alive for the complete local call.
    """
    capsule = bind(
        ctypes.pythonapi,
        "PyCapsule_GetPointer",
        ctypes.c_void_p,
        ctypes.py_object,
        ctypes.c_char_p,
    )
    native = bind(
        ctypes.CDLL("libgdk-3-0.dll"),
        "gdk_win32_window_get_handle",
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    value = native(capsule(window.__gpointer__, None))
    return int(value) if value else None


def explorer_tray(user: Any, *, exclude_pid: int) -> tuple[int, int] | None:
    """Find the real shell tray, excluding an owned notification receiver."""
    pointer, unsigned = ctypes.c_void_p, ctypes.c_uint32
    owner = bind(
        user, "GetWindowThreadProcessId", unsigned, pointer, ctypes.POINTER(unsigned)
    )
    desktop = bind(user, "GetShellWindow", pointer)()
    shell_pid = unsigned()
    if desktop:
        owner(desktop, ctypes.byref(shell_pid))
    find = bind(
        user,
        "FindWindowExW",
        pointer,
        pointer,
        pointer,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
    )
    previous = None
    for _ in range(32):
        window = find(None, previous, "Shell_TrayWnd", None)
        if not window:
            break
        pid = unsigned()
        owner(window, ctypes.byref(pid))
        if (
            pid.value
            and pid.value != exclude_pid
            and (not shell_pid.value or pid.value == shell_pid.value)
        ):
            return int(window), pid.value
        previous = window
    return None
