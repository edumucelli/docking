"""A window-scoped native close callback, removed before its owner is released."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes

from docking.log import get_logger

from .native import _bind

log = get_logger("windows.close_hook")


class WindowsCloseHook:
    """Defer an owned WM_CLOSE when GTK must first dismiss its own menu grab."""

    def __init__(self, on_close: Callable[[], bool]):
        self._on_close = on_close
        self._hwnd = None
        self._subclass_id = id(self)
        self._callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            ctypes.c_size_t,
            ctypes.c_size_t,
        )
        self._callback = self._callback_type(self._message)
        library = ctypes.WinDLL("comctl32", use_last_error=True)
        self._set = _bind(
            library,
            "SetWindowSubclass",
            wintypes.BOOL,
            wintypes.HWND,
            self._callback_type,
            ctypes.c_size_t,
            ctypes.c_size_t,
        )
        self._remove = _bind(
            library,
            "RemoveWindowSubclass",
            wintypes.BOOL,
            wintypes.HWND,
            self._callback_type,
            ctypes.c_size_t,
        )
        self._next = _bind(
            library,
            "DefSubclassProc",
            ctypes.c_ssize_t,
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )

    def install(self, hwnd: int) -> None:
        if self._hwnd == hwnd:
            return
        self.close()
        if not self._set(hwnd, self._callback, self._subclass_id, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        self._hwnd = hwnd

    def _message(self, hwnd, message, wparam, lparam, _subclass_id, _data):
        try:
            if message == 0x10 and self._on_close():  # WM_CLOSE
                return 0
            if message == 0x82:  # WM_NCDESTROY
                self.close()
        except Exception:
            # Never propagate a Python exception through a native window proc.
            log.exception("Native close callback failed")
        return self._next(hwnd, message, wparam, lparam)

    def close(self) -> None:
        if self._hwnd is not None:
            self._remove(self._hwnd, self._callback, self._subclass_id)
            self._hwnd = None
