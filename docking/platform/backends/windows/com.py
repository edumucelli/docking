"""Small COM boundary shared by native Windows shell integrations."""

from __future__ import annotations

import ctypes
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

from .ffi import bind as _bind

HRESULT = ctypes.c_int32


class GUID(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> GUID:
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def method(pointer: ctypes.c_void_p, index: int, result: Any, *arguments: Any) -> Any:
    table = ctypes.cast(
        pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *arguments)(table[index])


def release(pointer: ctypes.c_void_p) -> None:
    if pointer.value:
        method(pointer, 2, ctypes.c_uint32)(pointer)


@contextmanager
def apartment(*, multithreaded: bool = False) -> Iterator[Any]:
    ole = ctypes.WinDLL("ole32", use_last_error=True)
    initialize = _bind(ole, "CoInitializeEx", HRESULT, ctypes.c_void_p, ctypes.c_uint32)
    status = initialize(None, 0 if multithreaded else 2)
    # GTK/Gio may already own a different COM apartment on the UI thread.
    if status < 0 and status & 0xFFFFFFFF != 0x80010106:
        raise OSError(f"CoInitializeEx failed: 0x{status & 0xFFFFFFFF:08x}")
    try:
        yield ole
    finally:
        if status in (0, 1):
            _bind(ole, "CoUninitialize", None)()


def _check(status: int) -> None:
    if status < 0:
        raise OSError(status & 0xFFFFFFFF, f"COM failed: 0x{status & 0xFFFFFFFF:08x}")


def value(pointer, index: int, kind):
    if not pointer.value:
        raise OSError("Missing COM interface")
    result = kind()
    _check(
        method(pointer, index, HRESULT, ctypes.POINTER(kind))(
            pointer, ctypes.byref(result)
        )
    )
    return result


def object_result(stack: ExitStack, pointer, index: int, *arguments):
    if not pointer.value:
        raise OSError("Missing COM interface")
    result = ctypes.c_void_p()
    kinds, values = arguments[::2], arguments[1::2]
    try:
        _check(
            method(pointer, index, HRESULT, *kinds, ctypes.POINTER(ctypes.c_void_p))(
                pointer, *values, ctypes.byref(result)
            )
        )
    finally:
        if result.value:
            stack.callback(release, result)
    return result


def query(stack: ExitStack, pointer, interface: GUID):
    return object_result(
        stack, pointer, 0, ctypes.POINTER(GUID), ctypes.byref(interface)
    )
