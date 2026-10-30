"""Own a Shell-folder notification subscription on the GTK thread."""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from gi.repository import GLib

from docking.log import get_logger
from docking.platform.backends.windows.com import GUID, HRESULT
from docking.platform.backends.windows.ffi import bind as _bind


class _Entry(ctypes.Structure):
    _fields_ = [("pidl", ctypes.c_void_p), ("recursive", ctypes.c_int32)]


log = get_logger("windows.shell_changes")


class ShellChangeMonitor:
    """Defer shell notifications to GTK; unregister before releasing the HWND."""

    _MESSAGE = 0x8000 + 39

    def __init__(
        self, changed: Callable[[], None], *, folder_id: str, title: str
    ) -> None:
        self._changed = changed
        self._hwnd = None
        self._pidl = ctypes.c_void_p()
        self._registration = 0
        self._source = 0
        self._reconnect = False
        self._closed = False
        self._subclass_id = id(self)
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        controls = ctypes.WinDLL("comctl32", use_last_error=True)
        ole = ctypes.WinDLL("ole32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
            ctypes.c_size_t,
            ctypes.c_size_t,
        )
        self._callback = callback_type(self._message)
        self._set = _bind(
            controls,
            "SetWindowSubclass",
            ctypes.c_int32,
            ctypes.c_void_p,
            callback_type,
            ctypes.c_size_t,
            ctypes.c_size_t,
        )
        self._remove = _bind(
            controls,
            "RemoveWindowSubclass",
            ctypes.c_int32,
            ctypes.c_void_p,
            callback_type,
            ctypes.c_size_t,
        )
        self._next = _bind(
            controls,
            "DefSubclassProc",
            ctypes.c_ssize_t,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        )
        self._register = _bind(
            shell,
            "SHChangeNotifyRegister",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int32,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.POINTER(_Entry),
        )
        self._deregister = _bind(
            shell, "SHChangeNotifyDeregister", ctypes.c_int32, ctypes.c_uint32
        )
        self._lock = _bind(
            shell,
            "SHChangeNotification_Lock",
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_int32),
        )
        self._unlock = _bind(
            shell, "SHChangeNotification_Unlock", ctypes.c_int32, ctypes.c_void_p
        )
        self._free = _bind(ole, "CoTaskMemFree", None, ctypes.c_void_p)
        self._destroy = _bind(user, "DestroyWindow", ctypes.c_int32, ctypes.c_void_p)
        self._taskbar_created = _bind(
            user, "RegisterWindowMessageW", ctypes.c_uint32, ctypes.c_wchar_p
        )("TaskbarCreated")
        create = _bind(
            user,
            "CreateWindowExW",
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )
        folder = _bind(
            shell,
            "SHGetKnownFolderIDList",
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )
        try:
            # Invisible top-level windows receive TaskbarCreated broadcasts;
            # message-only windows do not. STATIC is a built-in window class.
            self._hwnd = create(
                0x08000080,
                "STATIC",
                title,
                0x80000000,
                0,
                0,
                0,
                0,
                None,
                None,
                None,
                None,
            )
            if not self._hwnd or not self._taskbar_created:
                raise ctypes.WinError(ctypes.get_last_error())
            if not self._set(self._hwnd, self._callback, self._subclass_id, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            folder_guid = GUID.parse(folder_id)
            status = folder(
                ctypes.byref(folder_guid), 0, None, ctypes.byref(self._pidl)
            )
            if status < 0:
                raise OSError(
                    f"Shell notification folder failed: 0x{status & 0xFFFFFFFF:08x}"
                )
            self._subscribe()
        except Exception:
            self.close()
            raise

    def _subscribe(self):
        if self._registration:
            self._deregister(self._registration)
            self._registration = 0
        entry = _Entry(self._pidl, 1)
        self._registration = self._register(
            self._hwnd, 0x8003, 0x7FFFFFFF, self._MESSAGE, 1, ctypes.byref(entry)
        )
        if not self._registration:
            raise OSError("The Windows shell rejected Shell folder monitoring")

    def _message(self, hwnd, message, wparam, lparam, _identity, _data):
        try:
            if message == self._MESSAGE and not self._closed:
                pidls, event = ctypes.c_void_p(), ctypes.c_int32()
                locked = self._lock(
                    wparam, lparam, ctypes.byref(pidls), ctypes.byref(event)
                )
                if locked:
                    try:
                        self._queue()
                    finally:
                        self._unlock(locked)
                return 0
            if message == self._taskbar_created and not self._closed:
                self._reconnect = True
                self._queue()
            if message == 0x82:  # WM_NCDESTROY, including unexpected destruction
                self._hwnd = None
                self.close()
        except Exception:
            log.exception("Windows Shell notification failed")
        return self._next(hwnd, message, wparam, lparam)

    def _queue(self):
        if not self._source:
            self._source = GLib.idle_add(self._notify)

    def _notify(self):
        self._source = 0
        if not self._closed:
            if self._reconnect:
                self._reconnect = False
                try:
                    self._subscribe()
                except OSError as exc:
                    log.warning("Could not reconnect Shell folder observer: %s", exc)
            self._changed()
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0
        if self._registration:
            self._deregister(self._registration)
            self._registration = 0
        if self._hwnd is not None:
            hwnd, self._hwnd = self._hwnd, None
            self._remove(hwnd, self._callback, self._subclass_id)
            self._destroy(hwnd)
        if self._pidl.value:
            self._free(self._pidl)
            self._pidl = ctypes.c_void_p()
