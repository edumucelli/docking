"""An owned Windows process tree that closes when its parent releases the job."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from docking.platform.native.windows.ffi import bind


class _Limits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _IO(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "read_operations",
            "write_operations",
            "other_operations",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _Limits),
        ("io", _IO),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class ProcessJob:
    """Assign before permitting the child to initialize its browser runtime."""

    def __init__(self) -> None:
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle = bind(
            self._kernel,
            "CreateJobObjectW",
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
        )(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE.
        if not bind(
            self._kernel,
            "SetInformationJobObject",
            wintypes.BOOL,
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        )(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process_handle: int) -> None:
        if not bind(
            self._kernel,
            "AssignProcessToJobObject",
            wintypes.BOOL,
            wintypes.HANDLE,
            wintypes.HANDLE,
        )(self._handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle:
            bind(self._kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)(
                self._handle
            )
            self._handle = None


class ProfileMutex:
    """Hold one profile's ownership across companion shutdown and replacement."""

    def __init__(self, name: str) -> None:
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle = bind(
            self._kernel,
            "CreateMutexW",
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        )(None, False, name)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        status = bind(
            self._kernel,
            "WaitForSingleObject",
            wintypes.DWORD,
            wintypes.HANDLE,
            wintypes.DWORD,
        )(self._handle, 0)
        self._owned = status in (0, 0x80)  # Acquired, or previous owner exited.
        if not self._owned:
            self.close()
            raise OSError("Browser profile is already in use")

    def close(self) -> None:
        if self._handle:
            if self._owned:
                bind(self._kernel, "ReleaseMutex", wintypes.BOOL, wintypes.HANDLE)(
                    self._handle
                )
                self._owned = False
            bind(self._kernel, "CloseHandle", wintypes.BOOL, wintypes.HANDLE)(
                self._handle
            )
            self._handle = None
