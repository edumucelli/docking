"""Explicit user-requested elevation for the fixed camera helper actions."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

from docking.platform.backends.windows.com import apartment
from docking.platform.backends.windows.ffi import bind

_P = ctypes.c_void_p
_U32 = ctypes.c_uint32


class _ExecuteInfo(ctypes.Structure):
    _fields_ = [
        ("size", _U32),
        ("mask", _U32),
        ("window", _P),
        ("verb", ctypes.c_wchar_p),
        ("file", ctypes.c_wchar_p),
        ("parameters", ctypes.c_wchar_p),
        ("directory", ctypes.c_wchar_p),
        ("show", ctypes.c_int32),
        ("instance", _P),
        ("id_list", _P),
        ("class_name", ctypes.c_wchar_p),
        ("class_key", _P),
        ("hotkey", _U32),
        ("icon", _P),
        ("process", _P),
    ]


def helper_available() -> bool:
    return Path(__file__).with_name("windows_helper.py").is_file()


def run_action(action: str) -> bool:
    if action not in ("lock", "unlock") or not helper_available():
        return False
    argv = [str(Path(__file__).with_name("windows_helper.py")), action]
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    if bind(shell, "IsUserAnAdmin", ctypes.c_int32)():
        try:
            result = subprocess.run(
                [sys.executable, *argv],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired as exc:
            raise OSError("Camera helper execution deadline expired") from exc
    info = _ExecuteInfo()
    info.size = ctypes.sizeof(info)
    info.mask = 0x40 | 0x100 | 0x400  # NOCLOSEPROCESS, NOASYNC, FLAG_NO_UI
    info.verb, info.file = "runas", sys.executable
    info.parameters = subprocess.list2cmdline(argv)
    info.show = 0
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        with apartment():
            launched = bind(
                shell, "ShellExecuteExW", ctypes.c_int32, ctypes.POINTER(_ExecuteInfo)
            )(ctypes.byref(info))
            error = ctypes.get_last_error()
        if not launched:
            if error == 1223:  # User cancelled elevation.
                return False
            raise ctypes.WinError(error)
        if not info.process:
            raise OSError("Missing elevated camera helper process")
        wait = bind(kernel, "WaitForSingleObject", _U32, _P, _U32)(info.process, 120000)
        if wait == 0x102:
            bind(kernel, "TerminateProcess", ctypes.c_int32, _P, _U32)(
                info.process, 124
            )
            bind(kernel, "WaitForSingleObject", _U32, _P, _U32)(info.process, 10000)
            return False
        if wait != 0:
            raise OSError("Camera helper wait failed")
        code = _U32()
        if not bind(
            kernel, "GetExitCodeProcess", ctypes.c_int32, _P, ctypes.POINTER(_U32)
        )(info.process, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value == 0
    finally:
        if info.process:
            bind(kernel, "CloseHandle", ctypes.c_int32, _P)(info.process)
