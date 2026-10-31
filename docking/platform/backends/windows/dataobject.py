"""Native OLE data objects populated only from already-prepared byte values."""

from __future__ import annotations

import ctypes
import struct
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager

from .com import GUID, HRESULT, apartment, method, release
from .ffi import bind


class FormatEtc(ctypes.Structure):
    _fields_ = (
        ("format", ctypes.c_uint16),
        ("device", ctypes.c_void_p),
        ("aspect", ctypes.c_uint32),
        ("index", ctypes.c_int32),
        ("medium", ctypes.c_uint32),
    )


class StorageMedium(ctypes.Structure):
    _fields_ = (
        ("kind", ctypes.c_uint32),
        ("data", ctypes.c_void_p),
        ("owner", ctypes.c_void_p),
    )


def _check(status: int) -> None:
    if status < 0:
        raise OSError(status & 0xFFFFFFFF, "Could not prepare native drag data")


def file_drop_bytes(paths: Sequence[str]) -> bytes:
    """Encode complete native paths in the Shell's Unicode CF_HDROP format."""
    if not paths or any(not path or "\0" in path for path in paths):
        raise ValueError("Native file selection must contain nonempty paths")
    return struct.pack("<IiiII", 20, 0, 0, 0, 1) + ("\0".join(paths) + "\0\0").encode(
        "utf-16-le"
    )


@contextmanager
def cached_data_object(
    formats: tuple[tuple[str, bytes], ...],
) -> Iterator[ctypes.c_void_p]:
    """Own a Shell data object without parsing paths or reading launcher files."""
    with apartment(), ExitStack() as stack:
        pointer = ctypes.c_void_p()
        stack.callback(release, pointer)
        interface = GUID.parse("0000010e-0000-0000-c000-000000000046")
        status = bind(
            ctypes.WinDLL("shell32", use_last_error=True),
            "SHCreateDataObject",
            HRESULT,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )(None, 0, None, None, ctypes.byref(interface), ctypes.byref(pointer))
        _check(status)
        if not pointer.value:
            raise OSError("Missing native drag data object")
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        allocate = bind(
            kernel, "GlobalAlloc", ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t
        )
        free = bind(kernel, "GlobalFree", ctypes.c_void_p, ctypes.c_void_p)
        lock = bind(kernel, "GlobalLock", ctypes.c_void_p, ctypes.c_void_p)
        unlock = bind(kernel, "GlobalUnlock", ctypes.c_int32, ctypes.c_void_p)
        register = bind(
            ctypes.WinDLL("user32", use_last_error=True),
            "RegisterClipboardFormatW",
            ctypes.c_uint32,
            ctypes.c_wchar_p,
        )
        for name, data in formats:
            if not data:
                raise ValueError("Native drag payload must not be empty")
            kind = 15 if name == "CF_HDROP" else register(name)
            if not kind:
                raise ctypes.WinError(ctypes.get_last_error())
            handle = allocate(0x42, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                address = lock(handle)
                if not address:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    ctypes.memmove(address, data, len(data))
                finally:
                    unlock(handle)
                request = FormatEtc(kind, None, 1, -1, 1)
                medium = StorageMedium(1, handle, None)
                _check(
                    method(
                        pointer,
                        7,
                        HRESULT,
                        ctypes.POINTER(FormatEtc),
                        ctypes.POINTER(StorageMedium),
                        ctypes.c_int32,
                    )(pointer, ctypes.byref(request), ctypes.byref(medium), True)
                )
                handle = None  # Successful SetData owns the global memory.
            finally:
                if handle is not None:
                    free(handle)
        yield pointer
