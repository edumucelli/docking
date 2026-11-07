"""NetBSD executable evidence through the kernel, independently of procfs."""

from __future__ import annotations

import ctypes
import errno
import os
from functools import cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psutil import Process


@cache
def _libc() -> ctypes.CDLL:
    library = ctypes.CDLL(None, use_errno=True)
    library.sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    library.sysctl.restype = ctypes.c_int
    return library


def _pathname(pid: int) -> str:
    """Read CTL_KERN/KERN_PROC_ARGS/pid/KERN_PROC_PATHNAME from NetBSD 11."""
    if pid <= 0:
        return ""
    # sys/sysctl.h: CTL_KERN=1, KERN_PROC_ARGS=48, KERN_PROC_PATHNAME=5.
    # https://man.netbsd.org/sysctl.3
    mib = (ctypes.c_int * 4)(1, 48, pid, 5)
    size = ctypes.c_size_t()
    library = _libc()
    if library.sysctl(mib, 4, None, ctypes.byref(size), None, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if not 0 < size.value <= 65536:
        raise OSError(errno.ENAMETOOLONG, "Invalid executable pathname length")
    buffer = ctypes.create_string_buffer(size.value)
    if library.sysctl(mib, 4, buffer, ctypes.byref(size), None, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return os.fsdecode(buffer.value)


def executable_path(process: Process) -> str:
    """Return kernel pathname evidence without psutil's procfs-based exe()."""
    return _pathname(process.pid)
