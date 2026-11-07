"""Thread-scoped WinRT strings, interface ownership and cancellable async reads."""

from __future__ import annotations

import ctypes
import threading
import time
from contextlib import ExitStack, contextmanager

from docking.log import get_logger
from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    method,
    query,
    release,
    value,
)
from docking.platform.native.windows.ffi import bind

log = get_logger("windows.winrt")
POINTER = ctypes.c_void_p
_ASYNC_INFO = GUID.parse("00000036-0000-0000-c000-000000000046")


def check(status: int) -> None:
    if status < 0:
        raise OSError(
            status & 0xFFFFFFFF, f"Windows Runtime failed: 0x{status & 0xFFFFFFFF:08x}"
        )


class WindowsRuntime:
    """Enter and exit on the same worker; never expose COM pointers to GTK."""

    def __init__(self) -> None:
        dll = ctypes.WinDLL("combase", use_last_error=True)
        self._initialize = bind(dll, "RoInitialize", HRESULT, ctypes.c_uint32)
        self._uninitialize = bind(dll, "RoUninitialize", None)
        self._create = bind(
            dll,
            "WindowsCreateString",
            HRESULT,
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.c_uint32,
            ctypes.POINTER(POINTER),
        )
        self._delete = bind(dll, "WindowsDeleteString", HRESULT, POINTER)
        self._raw = bind(
            dll,
            "WindowsGetStringRawBuffer",
            POINTER,
            POINTER,
            ctypes.POINTER(ctypes.c_uint32),
        )
        self._factory = bind(
            dll,
            "RoGetActivationFactory",
            HRESULT,
            POINTER,
            ctypes.POINTER(GUID),
            ctypes.POINTER(POINTER),
        )
        self._owner = 0
        self._initialized = False

    def __enter__(self):
        check(self._initialize(1))  # RO_INIT_MULTITHREADED; only our IO worker.
        self._owner = threading.get_ident()
        self._initialized = True
        return self

    def __exit__(self, *_exc):
        if threading.get_ident() != self._owner:
            raise RuntimeError("Windows Runtime apartment closed on another thread")
        if self._initialized:
            self._initialized = False
            self._uninitialize()

    @contextmanager
    def string(self, text: str):
        data = text.encode("utf-16-le")
        buffer = (ctypes.c_uint16 * (len(data) // 2 + 1)).from_buffer_copy(
            data + b"\0\0"
        )
        result = POINTER()
        try:
            check(self._create(buffer, len(data) // 2, ctypes.byref(result)))
            yield result
        finally:
            if result.value:
                self._delete(result)

    def factory(self, stack: ExitStack, name: str, interface: GUID):
        result = POINTER()
        with self.string(name) as text:
            try:
                check(
                    self._factory(text, ctypes.byref(interface), ctypes.byref(result))
                )
            finally:
                if result.value:
                    stack.callback(release, result)
        return result

    def text(self, pointer, index: int) -> str:
        result = POINTER()
        try:
            check(
                method(pointer, index, HRESULT, ctypes.POINTER(POINTER))(
                    pointer, ctypes.byref(result)
                )
            )
            length = ctypes.c_uint32()
            raw = self._raw(result, ctypes.byref(length))
            if length.value > 32768 or (length.value and not raw):
                raise OSError("Invalid Windows Runtime string")
            return (
                ctypes.string_at(raw, length.value * 2).decode(
                    "utf-16-le", errors="replace"
                )
                if length.value
                else ""
            )
        finally:
            if result.value:
                self._delete(result)

    @staticmethod
    def _close_operation(info) -> None:
        try:
            if value(info, 7, ctypes.c_int32).value == 0:
                check(method(info, 9, HRESULT)(info))  # Cancel
            check(method(info, 10, HRESULT)(info))  # Close
        except OSError as exc:
            log.debug("Could not close async Windows operation: %s", exc)

    def wait(
        self,
        stack: ExitStack,
        operation,
        cancelled: threading.Event,
        *,
        timeout: float = 3.0,
    ) -> None:
        info = query(stack, operation, _ASYNC_INFO)
        stack.callback(self._close_operation, info)
        deadline = time.monotonic() + timeout
        while True:
            if cancelled.is_set():
                raise OSError("Windows operation cancelled")
            status = value(info, 7, ctypes.c_int32).value
            if status == 1:
                return
            if status == 3:
                check(value(info, 8, HRESULT).value)
                raise OSError("Windows operation failed without an error code")
            if status == 2:
                raise OSError("Windows operation cancelled")
            if status != 0:
                raise OSError("Unknown Windows async operation status")
            if time.monotonic() >= deadline:
                raise TimeoutError("Windows operation timed out")
            cancelled.wait(min(0.01, max(0, deadline - time.monotonic())))
