"""Relay AppBar shared replies across the notification host process boundary.

WM_COPYDATA only carries input. Query/set-position and taskbar-position calls
also carry a shared-memory handle for their output. Explorer needs its own
shared allocation; forwarding the original packet loses those replies.
The wire record uses 32-bit HWNDs and a 64-bit shared handle on x64 Windows.
"""

from __future__ import annotations

import ctypes as c
import struct
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from docking.platform.native.windows.ffi import bind


class SharedMemory:
    def __init__(self) -> None:
        shell = c.WinDLL("shlwapi", use_last_error=True)
        self.allocate = bind(
            shell, "SHAllocShared", c.c_void_p, c.c_void_p, c.c_uint32, c.c_uint32
        )
        self.free = bind(shell, "SHFreeShared", c.c_int32, c.c_void_p, c.c_uint32)
        self._lock = bind(shell, "SHLockShared", c.c_void_p, c.c_void_p, c.c_uint32)
        self._unlock = bind(shell, "SHUnlockShared", c.c_int32, c.c_void_p)

    @contextmanager
    def lock(self, handle: int, pid: int) -> Iterator[int]:
        pointer = self._lock(handle, pid)
        if not pointer:
            raise OSError("Could not map an AppBar reply")
        try:
            yield int(pointer)
        finally:
            self._unlock(pointer)


class WindowsAppBarBridge:
    def __init__(self, *, memory: SharedMemory | None = None) -> None:
        self._memory = memory

    def forward(
        self, payload: bytes, explorer_pid: int, send: Callable[[bytes], int]
    ) -> int | None:
        if len(payload) != 64 or struct.unpack_from("<I", payload)[0] != 40:
            return None
        operation = struct.unpack_from("<I", payload, 40)[0]
        if operation not in (2, 3, 5):
            return None
        handle, source_pid = struct.unpack_from("<QI", payload, 48)
        if not handle or not source_pid or not explorer_pid:
            return 0
        if self._memory is None:
            self._memory = SharedMemory()
        memory = self._memory
        with memory.lock(handle, source_pid) as original:
            shared = memory.allocate(original, 40, explorer_pid)
            if not shared:
                raise OSError("Could not share an AppBar reply with Explorer")
            try:
                request = bytearray(payload)
                struct.pack_into("<QI", request, 48, shared, explorer_pid)
                result = send(bytes(request))
                if result:
                    with memory.lock(shared, explorer_pid) as reply:
                        c.memmove(original, reply, 40)
                return result
            finally:
                memory.free(shared, explorer_pid)
