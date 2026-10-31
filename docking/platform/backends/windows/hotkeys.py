"""Thread-owned Win32 hotkey registration and keyboard-layout translation."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .ffi import bind as _bind
from .input_context import GuiThreadInfo, typing_window
from .modifiers import AltGrGuard

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
HOTKEY_ID = 0x444F


class Message(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wparam", wintypes.WPARAM),
        ("lparam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("point", wintypes.POINT),
        ("private", wintypes.DWORD),
    ]


class WindowsHotkeyAPI:
    """Create, read and release one hotkey on the calling worker thread."""

    def __init__(self) -> None:
        self._preferred: tuple[int, int | str] | None = None
        self._binding: tuple[int, int] | None = None
        self._altgr = AltGrGuard()
        user = ctypes.WinDLL("user32", use_last_error=True)
        self._register = _bind(
            user,
            "RegisterHotKey",
            wintypes.BOOL,
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        )
        self._unregister = _bind(
            user,
            "UnregisterHotKey",
            wintypes.BOOL,
            wintypes.HWND,
            ctypes.c_int,
        )
        self._peek = _bind(
            user,
            "PeekMessageW",
            wintypes.BOOL,
            ctypes.POINTER(Message),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        )
        self._layout = _bind(user, "GetKeyboardLayout", wintypes.HANDLE, wintypes.DWORD)
        self._foreground = _bind(user, "GetForegroundWindow", wintypes.HWND)
        self._window_thread = _bind(
            user,
            "GetWindowThreadProcessId",
            wintypes.DWORD,
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        self._gui = _bind(
            user,
            "GetGUIThreadInfo",
            ctypes.c_int32,
            ctypes.c_uint32,
            ctypes.POINTER(GuiThreadInfo),
        )
        self._child = _bind(
            user, "IsChild", ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p
        )
        self._key_scan = _bind(
            user,
            "VkKeyScanExW",
            ctypes.c_short,
            wintypes.WCHAR,
            wintypes.HANDLE,
        )

    def register(self, modifiers: int, key: int | str) -> None:
        """Register a virtual key or a character in the foreground input layout."""
        self._preferred = modifiers, key
        # Peek creates this thread's queue before any messages can be posted.
        self._peek(ctypes.byref(Message()), None, 0, 0, 0)
        self._refresh()

    def _translate(self, modifiers: int, key: int | str) -> tuple[int, int]:
        if isinstance(key, str):
            hwnd = int(self._foreground() or 0)
            thread = self._window_thread(hwnd, None) if hwnd else 0
            window = typing_window(hwnd, thread, self._gui, self._child)
            if window != hwnd:
                thread = self._window_thread(window, None) or thread
            translated = int(self._key_scan(key, self._layout(thread)))
            if translated == -1 or translated & 0xFF00 & ~0x0700:
                raise ValueError("shortcut key is unavailable in the keyboard layout")
            shift = (translated >> 8) & 7
            key = translated & 0xFF
            modifiers |= (
                (MOD_SHIFT if shift & 1 else 0)
                | (MOD_CONTROL if shift & 2 else 0)
                | (MOD_ALT if shift & 4 else 0)
            )
        return modifiers, key

    def _refresh(self) -> None:
        if self._preferred is None:
            return
        self._altgr.check()
        try:
            binding = self._translate(*self._preferred)
        except ValueError:
            # Do not leave an unrelated key active when this layout cannot
            # represent the configured character. The worker retries later.
            self._release()
            raise
        if binding[0] & (MOD_CONTROL | MOD_ALT) == MOD_CONTROL | MOD_ALT:
            self._altgr.start(self._release)
            if self._altgr.active:
                self._release()
                return
        else:
            self._altgr.close()
        if binding == self._binding:
            return
        self._release()
        # The old registration can have queued activations. Retire those before
        # reusing its ID, including when a previously unavailable key recovers.
        while self._peek(ctypes.byref(Message()), None, WM_HOTKEY, WM_HOTKEY, 1):
            pass
        modifiers, key = binding
        if not self._register(None, HOTKEY_ID, modifiers | MOD_NOREPEAT, key):
            raise ctypes.WinError(ctypes.get_last_error())
        self._binding = binding

    def activation(self) -> int | None:
        """Refresh the foreground mapping and return one current activation."""
        if self._preferred is None:
            return None
        # Low-level modifier hooks are delivered through message retrieval.
        # Pump even while AltGr temporarily suspends the registered hotkey.
        self._peek(ctypes.byref(Message()), None, 0, 0, 0)
        self._refresh()
        if self._binding is None:
            return None
        message = Message()
        if (
            self._peek(ctypes.byref(message), None, WM_HOTKEY, WM_HOTKEY, 1)
            and message.wparam == HOTKEY_ID
        ):
            return int(message.time)
        return None

    def unregister(self) -> None:
        """Release the hotkey on the same thread that registered it."""
        self._preferred = None
        try:
            self._release()
        finally:
            self._altgr.close()

    def _release(self) -> None:
        if self._binding is None:
            return
        if not self._unregister(None, HOTKEY_ID):
            raise ctypes.WinError(ctypes.get_last_error())
        self._binding = None
