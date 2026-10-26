"""Reference-counted COM delegate lifetime across asynchronous native calls."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable
from typing import Any, ClassVar

from docking.log import get_logger

from .com import GUID, HRESULT

log = get_logger("windows.com_callback")
_POINTER = ctypes.c_void_p
_UNKNOWN = bytes(GUID.parse("00000000-0000-0000-c000-000000000046"))


class _Instance(ctypes.Structure):
    _fields_ = [("table", ctypes.POINTER(_POINTER))]


class ComCallback:
    """Keep Python thunks alive until native code releases its final reference.

    close() relinquishes the creator's reference. Each accepted async call or
    event subscription retains its own COM reference, including late replies.
    Callback exceptions are contained at this external ABI boundary.
    """

    _live: ClassVar[dict[int, ComCallback]] = {}
    _lock = threading.RLock()

    def __init__(
        self,
        iid: str,
        arguments: tuple[Any, ...],
        callback: Callable[..., Any],
        *,
        on_error: Callable[[Exception], None],
    ) -> None:
        self._iid = bytes(GUID.parse(iid))
        self._callback, self._on_error = callback, on_error
        self._references, self._owned = 1, True
        function = ctypes.WINFUNCTYPE
        self._functions = (
            function(HRESULT, _POINTER, ctypes.POINTER(GUID), ctypes.POINTER(_POINTER))(
                self._query
            ),
            function(ctypes.c_uint32, _POINTER)(self._addref),
            function(ctypes.c_uint32, _POINTER)(self._release),
            function(HRESULT, _POINTER, *arguments)(self._invoke),
        )
        self._table = (_POINTER * 4)(
            *(ctypes.cast(f, _POINTER).value for f in self._functions)
        )
        self._instance = _Instance(self._table)
        self._address = ctypes.addressof(self._instance)
        self.pointer = _POINTER(self._address)
        with self._lock:
            self._live[self._address] = self

    def _query(self, _this, iid, output):
        if not output or not iid:
            return -2147467261  # E_POINTER
        output[0] = None
        if bytes(iid.contents) not in (_UNKNOWN, self._iid):
            return -2147467262  # E_NOINTERFACE
        output[0] = self.pointer
        self._addref(_this)
        return 0

    def _addref(self, _this):
        with self._lock:
            self._references += 1
            return self._references

    def _release(self, _this):
        with self._lock:
            self._references -= 1
            if not self._references:
                self._live.pop(self._address, None)
            return self._references

    def _invoke(self, _this, *args):
        try:
            self._callback(*args)
            return 0
        except Exception as exc:
            try:
                self._on_error(exc)
            except Exception:
                log.error("COM callback error handler failed")
            return -2147467259  # E_FAIL

    def close(self) -> None:
        with self._lock:
            if self._owned:
                self._owned = False
                self._release(None)
