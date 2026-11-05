"""Windows keyboard toggle indicators read on the GTK message-loop thread."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from docking.applets.capslock.backend import LockKeyBackend
from docking.applets.capslock.state import LockKeyState
from docking.log import get_logger
from docking.platform.native.windows.ffi import bind

log = get_logger("capslock.windows")


class WindowsLockKeyBackend(LockKeyBackend):
    def __init__(self, *, read: Callable[[int], int] | None = None) -> None:
        self._read = read

    def get_state(self) -> LockKeyState:
        try:
            if self._read is None:
                self._read = bind(
                    ctypes.WinDLL("user32", use_last_error=True),
                    "GetKeyState",
                    ctypes.c_int16,
                    ctypes.c_int32,
                )
            # The high bit means currently pressed; only bit zero is the
            # toggle indicator. Zero is a valid reading for an off key.
            return LockKeyState(
                available=True,
                caps_lock=bool(self._read(0x14) & 1),
                num_lock=bool(self._read(0x90) & 1),
            )
        except OSError as exc:
            log.debug("Keyboard lock query failed: %s", exc)
            return LockKeyState(available=False)
