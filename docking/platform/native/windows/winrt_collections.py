"""Owned WinRT string iterables and typed property values at the COM boundary."""

from __future__ import annotations

import ctypes
import uuid
from contextlib import ExitStack

from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    method,
    object_result,
    query,
    value,
)
from docking.platform.native.windows.com_callback import ComObject
from docking.platform.native.windows.ffi import bind
from docking.platform.native.windows.winrt import WindowsRuntime, check

_P = ctypes.c_void_p
_NAMESPACE = uuid.UUID("11f47ad5-7b73-42c0-abae-878b1e16adee")
_INSPECTABLE = GUID.parse("af86e2e0-b12d-4c6a-9c5a-d7aa65101e90")
_PROPERTY = GUID.parse("4bd682dd-7554-40e9-9a9b-82654ede7e62")


def parameterized_iid(signature: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, signature))


def event_iid(sender: str, sender_iid: str, argument: str, argument_iid: str) -> str:
    return parameterized_iid(
        "pinterface({9de1c534-6ae1-11e0-84e1-18a905bcc53f};"
        f"rc({sender};{{{sender_iid}}});rc({argument};{{{argument_iid}}}))"
    )


class _Inspectable(ComObject):
    def _query(self, _this, iid, output):
        if iid and output and bytes(iid.contents) == bytes(_INSPECTABLE):
            output[0] = self.pointer
            self._addref(_this)
            return 0
        return super()._query(_this, iid, output)

    @staticmethod
    def _base():
        def ids(count, pointers):
            count[0], pointers[0] = 0, None

        def name(output):
            output[0] = None

        def trust(output):
            output[0] = 0

        return (
            ((ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(_P)), ids),
            ((ctypes.POINTER(_P),), name),
            ((ctypes.POINTER(ctypes.c_int32),), trust),
        )


class _StringIterator(_Inspectable):
    def __init__(self, runtime: WindowsRuntime, strings: tuple[str, ...]):
        self._index, self._strings, self._runtime = 0, strings, runtime
        self._duplicate = bind(
            ctypes.WinDLL("combase"),
            "WindowsDuplicateString",
            HRESULT,
            _P,
            ctypes.POINTER(_P),
        )
        super().__init__(
            parameterized_iid(
                "pinterface({6a79e863-4300-459a-9966-cbb660963ee1};string)"
            ),
            (
                *self._base(),
                ((ctypes.POINTER(_P),), self._current),
                ((ctypes.POINTER(ctypes.c_ubyte),), self._has_current),
                ((ctypes.POINTER(ctypes.c_ubyte),), self._move),
                (
                    (
                        ctypes.c_uint32,
                        ctypes.POINTER(_P),
                        ctypes.POINTER(ctypes.c_uint32),
                    ),
                    self._many,
                ),
            ),
            on_error=lambda _error: None,
            agile=True,
        )

    def _current(self, output):
        if self._index >= len(self._strings):
            return -2147483637  # E_BOUNDS
        with self._runtime.string(self._strings[self._index]) as text:
            check(self._duplicate(text, output))
        return 0

    def _has_current(self, output):
        output[0] = self._index < len(self._strings)

    def _move(self, output):
        self._index = min(self._index + 1, len(self._strings))
        self._has_current(output)

    def _many(self, capacity, output, count):
        count[0] = 0
        for index in range(min(capacity, len(self._strings) - self._index)):
            item = _P()
            self._current(ctypes.pointer(item))
            output[index] = item
            self._index += 1
            count[0] += 1


class StringIterable(_Inspectable):
    def __init__(self, runtime: WindowsRuntime, strings: tuple[str, ...]):
        self._runtime, self._strings = runtime, strings
        super().__init__(
            parameterized_iid(
                "pinterface({faa585ea-6214-4217-afda-7f46de5869b3};string)"
            ),
            (*self._base(), ((ctypes.POINTER(_P),), self._first)),
            on_error=lambda _error: None,
            agile=True,
        )

    def _first(self, output):
        iterator = _StringIterator(self._runtime, self._strings)
        # Transfer the creator reference to the native caller.
        output[0] = iterator.pointer


def property_value(
    runtime: WindowsRuntime, properties, name: str
) -> bool | int | str | None:
    """Read an optional property without coercing an unknown value to false/zero."""
    with ExitStack() as scope, runtime.string(name) as key:
        present = ctypes.c_ubyte()
        check(
            method(properties, 8, HRESULT, _P, ctypes.POINTER(ctypes.c_ubyte))(
                properties, key, ctypes.byref(present)
            )
        )
        if not present.value:
            return None
        boxed = object_result(scope, properties, 6, _P, key)
        if not boxed.value:
            return None
        typed = query(scope, boxed, _PROPERTY)
        kind = value(typed, 6, ctypes.c_int32).value
        if kind == 11:
            return bool(value(typed, 18, ctypes.c_ubyte).value)
        if kind == 12:
            return runtime.text(typed, 19)
        if kind == 16:
            return str(uuid.UUID(bytes_le=bytes(value(typed, 20, GUID))))
        numbers = {
            1: (8, ctypes.c_ubyte),
            2: (9, ctypes.c_int16),
            3: (10, ctypes.c_uint16),
            4: (11, ctypes.c_int32),
            5: (12, ctypes.c_uint32),
            6: (13, ctypes.c_int64),
            7: (14, ctypes.c_uint64),
        }
        if kind in numbers:
            index, native_type = numbers[kind]
            return int(value(typed, index, native_type).value)
        return None
