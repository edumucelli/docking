"""Native desktop input and observations shared by Windows acceptance tools."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import ClassVar


class _Mouse(ctypes.Structure):
    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
        ("data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class _Keyboard(ctypes.Structure):
    _fields_ = [
        ("key", wintypes.WORD),
        ("scan", wintypes.WORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra", ctypes.c_size_t),
    ]


class _Data(ctypes.Union):
    _fields_: ClassVar[list[tuple[str, type[ctypes.Structure]]]] = [
        ("mouse", _Mouse),
        ("keyboard", _Keyboard),
    ]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", _Data)]


class WindowsDesktop:
    """Send real desktop events and independently observe visible HWNDs."""

    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self._callback = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        self.user.EnumWindows.argtypes = (self._callback, wintypes.LPARAM)
        self.user.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        )
        self.user.GetWindowTextW.argtypes = (
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        self.user.GetClassNameW.argtypes = (
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        )
        self.user.GetWindowRect.argtypes = (
            wintypes.HWND,
            ctypes.POINTER(wintypes.RECT),
        )
        self.user.IsWindowVisible.argtypes = (wintypes.HWND,)
        self.user.IsWindow.argtypes = (wintypes.HWND,)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.SetCursorPos.argtypes = (ctypes.c_int, ctypes.c_int)
        self.user.PostMessageW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self.user.SendInput.argtypes = (
            wintypes.UINT,
            ctypes.POINTER(_Input),
            ctypes.c_int,
        )
        self.user.SendInput.restype = wintypes.UINT

    def windows(self, pid=None):
        rows = []

        @self._callback
        def visit(hwnd, _data):
            if self.user.IsWindowVisible(hwnd):
                owner = wintypes.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if pid is None or owner.value == pid:
                    text = ctypes.create_unicode_buffer(1024)
                    cls = ctypes.create_unicode_buffer(256)
                    rect = wintypes.RECT()
                    self.user.GetWindowTextW(hwnd, text, len(text))
                    self.user.GetClassNameW(hwnd, cls, len(cls))
                    self.user.GetWindowRect(hwnd, ctypes.byref(rect))
                    rows.append(
                        {
                            "hwnd": hwnd,
                            "pid": owner.value,
                            "title": text.value,
                            "class": cls.value,
                            "rect": [rect.left, rect.top, rect.right, rect.bottom],
                        }
                    )
            return True

        self.user.EnumWindows(visit, 0)
        return rows

    def _send(self, values):
        assert self.user.SendInput(
            len(values), (_Input * len(values))(*values), ctypes.sizeof(_Input)
        ) == len(values)

    def key(self, key, *, control=False, alt=False, shift=False):
        extended = int(key in (*range(0x21, 0x29), 0x2D, 0x2E))
        keys = [
            modifier
            for enabled, modifier in ((control, 0x11), (alt, 0x12), (shift, 0x10))
            if enabled
        ]
        events = (
            [(modifier, 0) for modifier in keys]
            + [(key, extended), (key, extended | 2)]
            + [(modifier, 2) for modifier in reversed(keys)]
        )
        self._send(
            [
                _Input(type=1, data=_Data(keyboard=_Keyboard(key=code, flags=flags)))
                for code, flags in events
            ]
        )
        time.sleep(0.15)

    def click(self, x, y, *, right=False, control=False):
        self.user.SetCursorPos(int(x), int(y))
        time.sleep(0.3)
        values = []
        if control:
            values.append(_Input(type=1, data=_Data(keyboard=_Keyboard(key=0x11))))
        values += [
            _Input(type=0, data=_Data(mouse=_Mouse(flags=flag)))
            for flag in ((8, 16) if right else (2, 4))
        ]
        if control:
            values.append(
                _Input(type=1, data=_Data(keyboard=_Keyboard(key=0x11, flags=2)))
            )
        self._send(values)
        time.sleep(0.3)

    def type_text(self, text):
        encoded = text.encode("utf-16-le")
        units = [
            int.from_bytes(encoded[index : index + 2], "little")
            for index in range(0, len(encoded), 2)
        ]
        self._send(
            [
                _Input(
                    type=1,
                    data=_Data(keyboard=_Keyboard(scan=unit, flags=flag)),
                )
                for unit in units
                for flag in (4, 6)
            ]
        )

    def workarea(self):
        self.user.SystemParametersInfoW.argtypes = (
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            wintypes.UINT,
        )
        rect = wintypes.RECT()
        assert self.user.SystemParametersInfoW(0x30, 0, ctypes.byref(rect), 0)
        return [rect.left, rect.top, rect.right, rect.bottom]

    def scroll(self, amount=120):
        self._send([_Input(type=0, data=_Data(mouse=_Mouse(data=amount, flags=0x800)))])
        time.sleep(0.2)

    @staticmethod
    def wait_for(predicate, message, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.1)
        raise AssertionError(message)
