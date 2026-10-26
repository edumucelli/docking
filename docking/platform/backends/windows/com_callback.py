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
_AGILE = bytes(GUID.parse("94ea2b94-e9cc-49e0-c0ff-ee64ca8f5b90"))


class _Instance(ctypes.Structure):
    _fields_ = [("table", ctypes.POINTER(_POINTER))]


class ComObject:
    """Keep Python thunks alive until native code releases its final reference.

    close() relinquishes the creator's reference. Each accepted async call or
    event subscription retains its own COM reference, including late replies.
    Callback exceptions are contained at this external ABI boundary.
    """

    _live: ClassVar[dict[int, ComObject]] = {}
    _lock = threading.RLock()

    def __init__(
        self,
        iid: str,
        methods: tuple[tuple[tuple[Any, ...], Callable[..., Any]], ...],
        *,
        on_error: Callable[[Exception], None],
        agile: bool = False,
    ) -> None:
        self._iid = bytes(GUID.parse(iid))
        self._on_error, self._agile = on_error, agile
        self._references, self._owned = 1, True
        function = ctypes.WINFUNCTYPE
        self._functions: list[Any] = [
            function(HRESULT, _POINTER, ctypes.POINTER(GUID), ctypes.POINTER(_POINTER))(
                self._query
            ),
            function(ctypes.c_uint32, _POINTER)(self._addref),
            function(ctypes.c_uint32, _POINTER)(self._release),
        ]
        for arguments, callback in methods:

            def invoke(_this, *args, callback=callback):
                return self._invoke(callback, *args)

            self._functions.append(function(HRESULT, _POINTER, *arguments)(invoke))
        self._table = (_POINTER * len(self._functions))(
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
        accepted = (
            (_UNKNOWN, self._iid, _AGILE) if self._agile else (_UNKNOWN, self._iid)
        )
        if bytes(iid.contents) not in accepted:
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

    def _invoke(self, callback, *args):
        try:
            result = callback(*args)
            return 0 if result is None else result
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


class ComCallback(ComObject):
    """One-method COM delegate, with an optional agile MTA callback boundary."""

    def __init__(
        self,
        iid: str,
        arguments: tuple[Any, ...],
        callback: Callable[..., Any],
        *,
        on_error: Callable[[Exception], None],
        agile: bool = False,
    ) -> None:
        def invoke(*args):
            callback(*args)

        super().__init__(iid, ((arguments, invoke),), on_error=on_error, agile=agile)
