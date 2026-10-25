"""Serialize hook writers with POSIX flock or a Windows named mutex.

Windows byte-range file locks are mandatory and would deny ordinary dock
readers access to the preferences file. A mutex keeps the existing advisory
writer contract; its path-derived name is shared by hook processes/sessions.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path


@contextmanager
def exclusive_update(fd: int, path: Path) -> Iterator[None]:
    if sys.platform != "win32":
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
        return
    with _windows_mutex(path):
        yield


@contextmanager
def _windows_mutex(path: Path) -> Iterator[None]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
    create.restype = wintypes.HANDLE
    wait = kernel.WaitForSingleObject
    wait.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    wait.restype = wintypes.DWORD
    release = kernel.ReleaseMutex
    release.argtypes = (wintypes.HANDLE,)
    release.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    identity = os.path.normcase(os.path.realpath(path))
    name = (
        "Global\\Docking.AIUsage." + hashlib.sha256(os.fsencode(identity)).hexdigest()
    )
    handle = create(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = wait(handle, 0xFFFFFFFF)
        # An abandoned mutex is acquired: its former process died while owning
        # it. The caller's existing malformed-JSON recovery still applies.
        if result not in (0, 0x80):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield
        finally:
            if not release(handle):
                raise ctypes.WinError(ctypes.get_last_error())
    finally:
        close(handle)
