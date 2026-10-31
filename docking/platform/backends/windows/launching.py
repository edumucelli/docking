"""Shell activation with an owned, nonblocking process lifetime handle."""

from __future__ import annotations

import ctypes
import subprocess
import weakref
from collections.abc import Sequence
from pathlib import PureWindowsPath

from docking.platform.applications.launched_process import LaunchOutcome

from .com import apartment
from .ffi import bind


class ShellExecuteInfo(ctypes.Structure):
    _fields_ = (
        ("size", ctypes.c_uint32),
        ("mask", ctypes.c_uint32),
        ("window", ctypes.c_void_p),
        ("verb", ctypes.c_wchar_p),
        ("file", ctypes.c_wchar_p),
        ("parameters", ctypes.c_wchar_p),
        ("directory", ctypes.c_wchar_p),
        ("show", ctypes.c_int32),
        ("instance", ctypes.c_void_p),
        ("id_list", ctypes.c_void_p),
        ("class_name", ctypes.c_wchar_p),
        ("class_key", ctypes.c_void_p),
        ("hotkey", ctypes.c_uint32),
        ("icon_or_monitor", ctypes.c_void_p),
        ("process", ctypes.c_void_p),
    )


class WindowsLaunchedProcess:
    """Poll the exact shell-returned process, never a reopened or reused PID."""

    def __init__(self, handle: int) -> None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        close = bind(kernel, "CloseHandle", ctypes.c_int32, ctypes.c_void_p)
        self._handle = handle
        self._cleanup = weakref.finalize(self, close, handle)
        self._wait = bind(
            kernel,
            "WaitForSingleObject",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )
        self._exit_code = bind(
            kernel,
            "GetExitCodeProcess",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        self.pid = int(
            bind(kernel, "GetProcessId", ctypes.c_uint32, ctypes.c_void_p)(handle)
        )
        self._status: int | None = None
        if not self.pid:
            self._cleanup()
            raise OSError("Could not identify the shell-launched process")

    def poll(self) -> int | None:
        if self._status is not None:
            return self._status
        state = self._wait(self._handle, 0)
        if state == 258:  # WAIT_TIMEOUT: still running.
            return None
        if state != 0:
            raise OSError("Could not poll the shell-launched process")
        code = ctypes.c_uint32()
        if not self._exit_code(self._handle, ctypes.byref(code)):
            raise OSError("Could not read the shell-launched process exit code")
        self._status = code.value
        self._cleanup()
        return self._status


def launch_shortcut(target: str, *, arguments: Sequence[str] = ()) -> LaunchOutcome:
    """Delegate shortcut semantics to the shell and retain its optional handle."""
    if "\0" in target or any("\0" in value for value in arguments):
        return LaunchOutcome(False)
    if PureWindowsPath(target).is_absolute():
        target = target.replace("/", "\\")
    parameters = subprocess.list2cmdline(arguments) if arguments else None
    if (
        parameters is not None
        and len(parameters.encode("utf-16-le", errors="surrogatepass")) // 2 >= 32767
    ):
        return LaunchOutcome(False)
    with apartment():
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        execute = bind(
            shell, "ShellExecuteExW", ctypes.c_int32, ctypes.POINTER(ShellExecuteInfo)
        )
        info = ShellExecuteInfo(
            size=ctypes.sizeof(ShellExecuteInfo),
            # NOCLOSEPROCESS | FLAG_NO_UI: return errors without a modal Shell
            # dialog blocking GTK. Security prompts retain their native behavior.
            mask=0x40 | 0x400,
            verb="open",
            file=target,
            parameters=parameters,
            show=1,
        )
        if not execute(ctypes.byref(info)):
            # A failed call can still return a handle that belongs to us.
            if info.process:
                bind(
                    ctypes.WinDLL("kernel32"),
                    "CloseHandle",
                    ctypes.c_int32,
                    ctypes.c_void_p,
                )(info.process)
            return LaunchOutcome(False)
        if not info.process:
            # DDE and delegated activations need not create a new process.
            return LaunchOutcome(True)
        try:
            process = WindowsLaunchedProcess(info.process)
        except OSError:
            # Launch succeeded; unavailable process evidence is optional.
            return LaunchOutcome(True)
        return LaunchOutcome(True, process)
