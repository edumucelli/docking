"""Identity of a Windows application's current typing window."""

import ctypes
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class InputContext:
    root: int
    window: int
    thread: int
    pid: int
    control_surface: bool = False
    root_thread: int = 0
    root_pid: int = 0

    @property
    def key(self) -> str:
        thread, pid = self.root_thread or self.thread, self.root_pid or self.pid
        return f"{self.root:x}:{thread:x}:{pid:x}"


class _Rect(ctypes.Structure):
    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class GuiThreadInfo(ctypes.Structure):
    _fields_ = (
        [("size", ctypes.c_uint32), ("flags", ctypes.c_uint32)]
        + [
            (name, ctypes.c_void_p)
            for name in ("active", "focus", "capture", "menu", "move", "caret")
        ]
        + [("caret_rect", _Rect)]
    )


def typing_window(
    root: int,
    thread: int,
    gui: Callable[[int, object], int],
    child: Callable[[int, int], int],
) -> int:
    info = GuiThreadInfo()
    info.size = ctypes.sizeof(info)
    if (
        root
        and thread
        and gui(thread, ctypes.byref(info))
        and info.focus
        and (info.focus == root or child(root, info.focus))
    ):
        return int(info.focus)
    return root
