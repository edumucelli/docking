"""Native desktop input and observations shared by Windows acceptance tools."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import time
from ctypes import wintypes
from pathlib import Path
from typing import ClassVar


def read_shared_json(path: Path):
    """Retry transient Windows/SMB sharing failures during atomic replacement."""
    for _ in range(19):
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except PermissionError:
            time.sleep(0.01)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_shared_json(path: Path, value: dict) -> None:
    """Publish atomically while an SMB observer briefly holds the old report."""
    temporary = path.with_suffix(".tmp")
    data = json.dumps(value, indent=2) + "\n"
    for attempt in range(20):
        try:
            temporary.write_text(data, encoding="utf-8")
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.01)


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
        self.user.WindowFromPoint.argtypes = (wintypes.POINT,)
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        self.user.GetAncestor.restype = wintypes.HWND
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
        self.user.MapVirtualKeyExW.argtypes = (
            wintypes.UINT,
            wintypes.UINT,
            wintypes.HKL,
        )
        self.user.MapVirtualKeyExW.restype = wintypes.UINT
        self._dwm = ctypes.WinDLL("dwmapi")
        self._dwm.DwmGetWindowAttribute.argtypes = (
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        self._dwm.DwmGetWindowAttribute.restype = ctypes.c_int32

    def windows(self, pid=None, *, include_cloaked=False):
        """Exclude DWM-cloaked retained UWP frames from desktop observations."""
        rows = []

        @self._callback
        def visit(hwnd, _data):
            if self.user.IsWindowVisible(hwnd):
                cloaked = ctypes.c_uint32()
                if (
                    not include_cloaked
                    and self._dwm.DwmGetWindowAttribute(
                        hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
                    )
                    == 0
                    and cloaked.value
                ):
                    return True
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

    def explorer(self):
        """Identify the actual shell process, excluding Docking's hidden host."""
        from docking.platform.backends.windows.ffi import bind

        desktop = bind(self.user, "GetShellWindow", wintypes.HWND)()
        owner = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(desktop, ctypes.byref(owner))
        if not desktop or not owner.value:
            return None
        tray = next(
            (w for w in self.windows(owner.value) if w["class"] == "Shell_TrayWnd"),
            None,
        )
        if tray is None:
            return None
        style = bind(
            self.user,
            "GetWindowLongPtrW",
            ctypes.c_ssize_t,
            wintypes.HWND,
            ctypes.c_int,
        )(tray["hwnd"], -20)
        return {"pid": owner.value, "window": tray["hwnd"], "style": style}

    def ensure_explorer(self):
        """Recover the owned test desktop if its shell has not auto-restarted."""
        if self.explorer() is None:
            subprocess.Popen(
                [str(Path(os.environ["WINDIR"]) / "explorer.exe")],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        return self.wait_for(self.explorer, "Explorer did not become ready", 30)

    def restart_explorer(self):
        """Simulate a shell crash only on an explicitly selected test desktop.

        Refuse open folder windows, validate the exact shell executable, and
        terminate only that process. psutil guards against PID reuse. The caller
        must recover the desktop after closing its dock if Explorer cannot start
        while a replacement notification host exists.
        """
        import psutil

        before = self.explorer()
        assert before is not None, "Explorer must be running before this check"
        assert not any(w["class"] == "CabinetWClass" for w in self.windows()), (
            "Close Explorer folder windows before testing a shell crash"
        )
        process = psutil.Process(before["pid"])
        assert Path(process.exe()).samefile(Path(os.environ["WINDIR"]) / "explorer.exe")
        assert self.explorer() == before
        process.kill()
        process.wait(15)
        deadline = time.monotonic() + 3
        while self.explorer() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        after = self.ensure_explorer()
        assert after["pid"] != before["pid"]
        return {"before": before, "after": after, "old_process_exited": True}

    def _send(self, values):
        assert self.user.SendInput(
            len(values), (_Input * len(values))(*values), ctypes.sizeof(_Input)
        ) == len(values)

    def key(self, key, *, control=False, alt=False, shift=False, windows=False):
        extended = int(key in (*range(0x21, 0x29), 0x2D, 0x2E))
        keys = [
            modifier
            for enabled, modifier in (
                (control, 0x11),
                (alt, 0x12),
                (shift, 0x10),
                (windows, 0x5B),
            )
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

    def click(self, x, y, *, right=False, control=False, before_press=None):
        assert self.user.SetCursorPos(int(x), int(y)), (
            "Pointer did not move before click"
        )
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
        if before_press is not None:
            before_press()
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

    def double_click(self, x, y):
        self.user.SetCursorPos(int(x), int(y))
        time.sleep(0.3)
        self._send(
            [
                _Input(type=0, data=_Data(mouse=_Mouse(flags=flag)))
                for flag in (2, 4, 2, 4)
            ]
        )
        time.sleep(0.3)

    def drag(self, start, end):
        """Drag with actual pointer input, including protected native captions."""
        self.user.SetCursorPos(int(start[0]), int(start[1]))
        time.sleep(0.2)
        self._send([_Input(type=0, data=_Data(mouse=_Mouse(flags=2)))])
        try:
            for step in range(1, 13):
                self.user.SetCursorPos(
                    int(start[0] + (end[0] - start[0]) * step / 12),
                    int(start[1] + (end[1] - start[1]) * step / 12),
                )
                time.sleep(0.05)
        finally:
            self._send([_Input(type=0, data=_Data(mouse=_Mouse(flags=4)))])
        time.sleep(0.3)

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

    def window_at(self, x, y):
        handle = self.user.WindowFromPoint(wintypes.POINT(int(x), int(y)))
        return self.user.GetAncestor(handle, 2) if handle else None

    def scroll(self, amount=120):
        self._send([_Input(type=0, data=_Data(mouse=_Mouse(data=amount, flags=0x800)))])
        time.sleep(0.2)

    def scan(self, code, *, extended=False, altgr=False, mapped_vk=False, layout=None):
        """Press a key position, optionally comparing layout-mapped VK routing.

        The default uses KEYEVENTF_SCANCODE with wVk=0. The comparison uses
        MapVirtualKeyExW on the caller-supplied target HKL and retains wScan,
        without KEYEVENTF_SCANCODE (which would make Windows ignore wVk).
        """
        if mapped_vk and not layout:
            raise ValueError("Mapped virtual keys require the selected target layout")

        def key(scan, is_extended):
            if not mapped_vk:
                return 0
            value = self.user.MapVirtualKeyExW(
                scan | (0xE000 if is_extended else 0), 3, layout
            )  # MAPVK_VSC_TO_VK_EX preserves left/right modifier identity.
            if not 1 <= value <= 254:
                raise ValueError("The target layout did not map the physical key")
            return int(value)

        flags = (0 if mapped_vk else 8) | int(extended)
        virtual = key(code, extended)
        events = [
            _Input(
                type=1,
                data=_Data(keyboard=_Keyboard(key=virtual, scan=code, flags=flags)),
            ),
            _Input(
                type=1,
                data=_Data(keyboard=_Keyboard(key=virtual, scan=code, flags=flags | 2)),
            ),
        ]
        if altgr:
            modifier, modifier_flags = key(0x38, True), (0 if mapped_vk else 8) | 1
            events.insert(
                0,
                _Input(
                    type=1,
                    data=_Data(
                        keyboard=_Keyboard(
                            key=modifier, scan=0x38, flags=modifier_flags
                        )
                    ),
                ),
            )
            events.append(
                _Input(
                    type=1,
                    data=_Data(
                        keyboard=_Keyboard(
                            key=modifier, scan=0x38, flags=modifier_flags | 2
                        )
                    ),
                )
            )
        self._send(events)
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
