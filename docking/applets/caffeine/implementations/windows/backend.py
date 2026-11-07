"""Caffeine's Windows implementation of the shared Inhibitor contract.

Own display/system power-request counts and their handle. These requests do
not synthesize user input or change persistent power, lock or screensaver policy.
"""

from __future__ import annotations

import ctypes
from typing import ClassVar

from docking.applets.caffeine.inhibit import Inhibitor
from docking.i18n import _
from docking.log import get_logger
from docking.platform.native.windows.ffi import bind

log = get_logger("caffeine.windows")


class _DetailedReason(ctypes.Structure):
    _fields_ = [
        ("module", ctypes.c_void_p),
        ("identifier", ctypes.c_uint32),
        ("count", ctypes.c_uint32),
        ("strings", ctypes.POINTER(ctypes.c_wchar_p)),
    ]


class _Reason(ctypes.Union):
    _fields_: ClassVar[list[tuple[str, type]]] = [
        ("detailed", _DetailedReason),
        ("simple", ctypes.c_wchar_p),
    ]


class ReasonContext(ctypes.Structure):
    _fields_ = [
        ("version", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("reason", _Reason),
    ]


class WindowsPowerRequests:
    def __init__(self) -> None:
        library = ctypes.WinDLL("kernel32", use_last_error=True)
        self._create = bind(
            library,
            "PowerCreateRequest",
            ctypes.c_void_p,
            ctypes.POINTER(ReasonContext),
        )
        self._set = bind(
            library, "PowerSetRequest", ctypes.c_int32, ctypes.c_void_p, ctypes.c_int32
        )
        self._clear = bind(
            library,
            "PowerClearRequest",
            ctypes.c_int32,
            ctypes.c_void_p,
            ctypes.c_int32,
        )
        self._close = bind(library, "CloseHandle", ctypes.c_int32, ctypes.c_void_p)

    def create(self, reason: str) -> int:
        context = ReasonContext(0, 1, _Reason(simple=reason))
        handle = self._create(ctypes.byref(context))
        if not handle or handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        return int(handle)

    def set(self, handle: int, kind: int) -> None:
        if not self._set(handle, kind):
            raise ctypes.WinError(ctypes.get_last_error())

    def clear(self, handle: int, kind: int) -> None:
        if not self._clear(handle, kind):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self, handle: int) -> None:
        if not self._close(handle):
            raise ctypes.WinError(ctypes.get_last_error())


class WindowsInhibitor(Inhibitor):
    """Called serially by InhibitionSession, including cleanup after stop."""

    def __init__(self, *, api: WindowsPowerRequests | None = None) -> None:
        self._api = api
        self._handle: int | None = None
        self._held: set[int] = set()

    @property
    def active(self) -> bool:
        return bool(self._held)

    def acquire(self) -> bool:
        if self._held == {0, 1}:
            return True
        if self._handle is not None:
            self.release()
            if self._handle is not None:
                return False
        try:
            if self._api is None:
                self._api = WindowsPowerRequests()
            self._handle = self._api.create(_("Caffeine: keeping awake"))
            for kind in (0, 1):  # PowerRequestDisplayRequired, SystemRequired.
                self._api.set(self._handle, kind)
                self._held.add(kind)
            return True
        except OSError as exc:
            log.debug("Could not acquire Windows wake requests: %s", exc)
            self.release()
            return False

    def release(self) -> None:
        if self._handle is None:
            return
        assert self._api is not None
        for kind in tuple(self._held):
            try:
                self._api.clear(self._handle, kind)
                self._held.remove(kind)
            except OSError as exc:
                # Closing the owned object also releases any remaining counts.
                log.debug("Could not clear wake request: %s", exc)
        try:
            self._api.close(self._handle)
        except OSError as exc:
            # Retain ownership so another release can retry instead of losing
            # the only reference to a still-live request object.
            log.debug("Could not close wake request handle: %s", exc)
        else:
            self._handle = None
            self._held.clear()
