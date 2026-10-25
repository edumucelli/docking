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
