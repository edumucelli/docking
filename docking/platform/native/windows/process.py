"""Scoped process metadata observation without application registry policy."""

from __future__ import annotations

import ctypes
import importlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PureWindowsPath
from typing import Any

from docking.platform.native.windows.ffi import bind, windows_ctypes

_POINTER = ctypes.c_void_p
_UNSIGNED = ctypes.c_uint32


def psutil_module() -> Any:
    """Load the optional native process dependency only when queried."""
    return importlib.import_module("psutil")


@dataclass(frozen=True)
class WindowsProcessMetadata:
    executable_path: PureWindowsPath | None
    aumid: str | None


class _ProcessAPI:
    """Bind once, but borrow one newly opened process handle per observation."""

    def __init__(self) -> None:
        kernel = windows_ctypes.WinDLL("kernel32", use_last_error=True)
        self._open = bind(
            kernel, "OpenProcess", _POINTER, _UNSIGNED, ctypes.c_int32, _UNSIGNED
        )
        self._close = bind(kernel, "CloseHandle", ctypes.c_int32, _POINTER)
        self._executable = bind(
            kernel,
            "QueryFullProcessImageNameW",
            ctypes.c_int32,
            _POINTER,
            _UNSIGNED,
            ctypes.c_wchar_p,
            ctypes.POINTER(_UNSIGNED),
        )
        self._aumid = bind(
            kernel,
            "GetApplicationUserModelId",
            ctypes.c_int32,
            _POINTER,
            ctypes.POINTER(_UNSIGNED),
            ctypes.c_wchar_p,
        )

    def _path(self, process: int) -> PureWindowsPath | None:
        size = _UNSIGNED(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        try:
            if (
                self._executable(process, 0, buffer, ctypes.byref(size))
                and buffer.value
            ):
                return PureWindowsPath(buffer.value)
        except OSError:
            pass
        return None

    def _application_id(self, process: int) -> str | None:
        size = _UNSIGNED()
        try:
            if (
                self._aumid(process, ctypes.byref(size), None) == 122
                and 0 < size.value <= 4096
            ):
                buffer = ctypes.create_unicode_buffer(size.value)
                if self._aumid(process, ctypes.byref(size), buffer) == 0:
                    return str(buffer.value) or None
        except OSError:
            pass
        return None

    def read(self, pid: int, *, include_aumid: bool) -> WindowsProcessMetadata | None:
        process = self._open(0x1000, False, pid)  # query limited information
        if not process:
            return None
        try:
            return WindowsProcessMetadata(
                executable_path=self._path(process),
                aumid=self._application_id(process) if include_aumid else None,
            )
        finally:
            self._close(process)


@lru_cache(maxsize=1)
def _process_api() -> _ProcessAPI:
    # Cache only function bindings. A PID may refer to a different process later.
    return _ProcessAPI()


def read_process_metadata(
    pid: int, *, include_aumid: bool = True
) -> WindowsProcessMetadata | None:
    """Read related facts through one handle; unavailable fields remain absent."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        return _process_api().read(pid, include_aumid=include_aumid)
    except OSError:
        return None


class NativeProcessReader:
    def executable(self, pid: int) -> Path | None:
        metadata = read_process_metadata(pid, include_aumid=False)
        return (
            Path(str(metadata.executable_path))
            if metadata is not None and metadata.executable_path is not None
            else None
        )
