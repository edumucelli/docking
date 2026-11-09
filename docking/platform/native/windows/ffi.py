"""Small Win32 ABI primitives shared by Windows service implementations."""

# Match the external Windows API names in the module typing views.
# ruff: noqa: N802

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from typing import Any, Protocol, cast


class _WindowsCtypes(Protocol):
    """Windows-only ctypes APIs omitted by Linux's standard-library stubs."""

    def WinDLL(self, name: str, *, use_last_error: bool = False) -> ctypes.CDLL: ...
    def WinError(self, code: int | None = None) -> OSError: ...
    def get_last_error(self) -> int: ...
    def set_last_error(self, value: int) -> int: ...
    def WINFUNCTYPE(self, result: object, *arguments: object) -> Any: ...


class _WindowsOS(Protocol):
    def add_dll_directory(self, path: str) -> object: ...
    def startfile(
        self,
        path: str,
        operation: str = "open",
        *,
        arguments: str = "",
    ) -> None: ...


class _RegistryKey(Protocol):
    def __enter__(self) -> _RegistryKey: ...
    def __exit__(self, *exception: object) -> None: ...
    def Detach(self) -> int: ...


class _Registry(Protocol):
    HKEY_LOCAL_MACHINE: int
    HKEY_CURRENT_USER: int
    KEY_READ: int
    KEY_SET_VALUE: int
    KEY_WOW64_64KEY: int
    REG_SZ: int
    REG_DWORD: int

    def OpenKey(
        self, key: object, _subkey: str, reserved: int = 0, access: int = 0x20019
    ) -> _RegistryKey: ...
    def CreateKey(self, key: object, _subkey: str) -> _RegistryKey: ...
    def CreateKeyEx(
        self, key: object, _subkey: str, reserved: int = 0, access: int = 0x20006
    ) -> _RegistryKey: ...
    def CloseKey(self, key: object) -> None: ...
    def QueryValueEx(self, key: object, name: str) -> tuple[Any, int]: ...
    def SetValueEx(
        self, key: object, name: str, reserved: int, kind: int, value: object
    ) -> None: ...
    def DeleteValue(self, key: object, name: str) -> None: ...
    def FlushKey(self, key: object) -> None: ...
    def QueryInfoKey(self, key: object) -> tuple[int, int, int]: ...
    def EnumKey(self, key: object, index: int) -> str: ...


class WindowsProcessHandle(Protocol):
    """The native handle provided by Windows' subprocess.Popen."""

    _handle: int


class WindowsErrorCode(Protocol):
    winerror: int


class _GObjectPointer(Protocol):
    __gpointer__: object


# The module objects are opaque at this cross-platform boundary. These views
# retain those original objects, including ctypes mocking and CPython's
# WinDLL/WINFUNCTYPE calling conventions.
windows_ctypes = cast(_WindowsCtypes, cast(object, ctypes))
windows_os = cast(_WindowsOS, cast(object, os))
CREATE_NO_WINDOW = 0x08000000


def registry() -> _Registry:
    """Keep registry loading lazy on hosts that do not supply winreg."""
    import winreg

    return cast(_Registry, cast(object, winreg))


class MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("monitor", wintypes.RECT),
        ("workarea", wintypes.RECT),
        ("flags", wintypes.DWORD),
    ]


class ProcessMachineInformation(ctypes.Structure):
    # SDK processthreadsapi.h, ProcessMachineTypeInfo (9), Windows build 22000+.
    _fields_ = [
        ("machine", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16),
        ("attributes", ctypes.c_uint32),
    ]


def bind(library: Any, name: str, result: Any, *arguments: Any) -> Any:
    function = getattr(library, name)
    function.restype = result
    function.argtypes = arguments
    return function


def process_machine(kernel: Any, process: int) -> int:
    """Read a borrowed process handle's ABI, including AMD64 emulation on Arm.

    x64 on Arm uses the host namespace, not WOW64, so UNKNOWN/native ARM64
    cannot identify its ABI. Resolve that case with ProcessMachineTypeInfo;
    older unambiguous x86/AMD64 results keep their existing Windows API floor.
    An unavailable or failed query never authorizes a guessed hook architecture.
    """
    guest, native = ctypes.c_uint16(), ctypes.c_uint16()
    if not bind(
        kernel,
        "IsWow64Process2",
        ctypes.c_int32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(ctypes.c_uint16),
    )(process, ctypes.byref(guest), ctypes.byref(native)):
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())
    if guest.value or native.value != 0xAA64:
        return guest.value or native.value
    info = ProcessMachineInformation()
    try:
        query = bind(
            kernel,
            "GetProcessInformation",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.POINTER(ProcessMachineInformation),
            ctypes.c_uint32,
        )
    except AttributeError:
        raise OSError("Process machine type query is unavailable") from None
    if not query(process, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise windows_ctypes.WinError(windows_ctypes.get_last_error())
    return int(info.machine)


def window_handle(window: object) -> int | None:
    """Resolve a live GdkWindow through GDK's exported Win32 ABI.

    GTK 3 does not introspect this function. PyGObject's capsule exposes the
    borrowed GObject pointer; keep its owner alive for the complete local call.
    """
    capsule = bind(
        ctypes.pythonapi,
        "PyCapsule_GetPointer",
        ctypes.c_void_p,
        ctypes.py_object,
        ctypes.c_char_p,
    )
    native = bind(
        ctypes.CDLL("libgdk-3-0.dll"),
        "gdk_win32_window_get_handle",
        ctypes.c_void_p,
        ctypes.c_void_p,
    )
    value = native(capsule(cast(_GObjectPointer, window).__gpointer__, None))
    return int(value) if value else None


def explorer_tray(user: Any, *, exclude_pid: int) -> tuple[int, int] | None:
    """Find the real shell tray, excluding an owned notification receiver."""
    pointer, unsigned = ctypes.c_void_p, ctypes.c_uint32
    owner = bind(
        user, "GetWindowThreadProcessId", unsigned, pointer, ctypes.POINTER(unsigned)
    )
    desktop = bind(user, "GetShellWindow", pointer)()
    shell_pid = unsigned()
    if desktop:
        owner(desktop, ctypes.byref(shell_pid))
    find = bind(
        user,
        "FindWindowExW",
        pointer,
        pointer,
        pointer,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
    )
    previous = None
    for _ in range(32):
        window = find(None, previous, "Shell_TrayWnd", None)
        if not window:
            break
        pid = unsigned()
        owner(window, ctypes.byref(pid))
        if (
            pid.value
            and pid.value != exclude_pid
            and (not shell_pid.value or pid.value == shell_pid.value)
        ):
            return int(window), pid.value
        previous = window
    return None
