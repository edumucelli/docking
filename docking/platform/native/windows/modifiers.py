"""Distinguish physical AltGr from Ctrl+Alt without consuming keyboard input."""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from functools import lru_cache

from docking.platform.native.windows.ffi import bind


@lru_cache(maxsize=1)
def _key_state():
    return bind(
        ctypes.WinDLL("user32", use_last_error=True),
        "GetKeyState",
        ctypes.c_int16,
        ctypes.c_int32,
    )


def altgr_pressed() -> bool:
    # Queue-relative state belongs to the GTK thread processing this key event.
    return bool(_key_state()(0xA5) & 0x8000)  # VK_RMENU


class _Key(ctypes.Structure):
    _fields_ = [
        ("virtual_key", ctypes.c_uint32),
        ("scan_code", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("extra", ctypes.c_size_t),
    ]


class AltGrGuard:
    """Own a low-level modifier hook on the hotkey worker's message thread.

    The hook observes only right Alt transitions and always passes every event
    onward. It never records typed keys or runs on another application's thread.
    """

    def __init__(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int32, ctypes.c_size_t, ctypes.c_ssize_t
        )
        self._install = bind(
            user,
            "SetWindowsHookExW",
            ctypes.c_void_p,
            ctypes.c_int32,
            self._callback_type,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        self._remove = bind(
            user, "UnhookWindowsHookEx", ctypes.c_int32, ctypes.c_void_p
        )
        self._next = bind(
            user,
            "CallNextHookEx",
            ctypes.c_ssize_t,
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        self._module = bind(
            kernel, "GetModuleHandleW", ctypes.c_void_p, ctypes.c_wchar_p
        )
        self._key_state = bind(user, "GetAsyncKeyState", ctypes.c_int16, ctypes.c_int32)
        self._hook = None
        self._callback = None
        self._on_press: Callable[[], None] = lambda: None
        self._down = False
        self._error: OSError | None = None

    @property
    def active(self) -> bool:
        return self._down

    def check(self) -> None:
        error, self._error = self._error, None
        if error is not None:
            raise error

    def start(self, on_press: Callable[[], None]) -> None:
        if self._hook is not None:
            return
        self._on_press = on_press
        self._down = bool(self._key_state(0xA5) & 0x8000)  # VK_RMENU
        self._callback = self._callback_type(self._event)
        hook = self._install(13, self._callback, self._module(None), 0)
        if not hook:
            self._callback = None
            self._on_press = lambda: None
            raise ctypes.WinError(ctypes.get_last_error())
        self._hook = hook

    def _event(self, code: int, message: int, address: int) -> int:
        try:
            if code >= 0:
                key = ctypes.cast(address, ctypes.POINTER(_Key)).contents
                if key.virtual_key == 0xA5 or (
                    key.virtual_key == 0x12 and key.flags & 1
                ):
                    if message in (0x100, 0x104):  # WM_KEYDOWN / WM_SYSKEYDOWN
                        self._down = True
                        self._on_press()
                    elif message in (0x101, 0x105):
                        self._down = False
        except Exception as error:
            # This external callback must forward input even if its Python
            # client fails. Report the failure on the owning worker instead.
            self._error = OSError("Windows modifier callback failed")
            self._error.__cause__ = error
        return int(self._next(self._hook, code, message, address))

    def close(self) -> None:
        if self._hook is not None:
            if not self._remove(self._hook):
                raise ctypes.WinError(ctypes.get_last_error())
            self._hook = None
        self._callback = None
        self._on_press = lambda: None
        self._down = False
        self._error = None
