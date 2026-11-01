"""Bounded battery-service reads on a GATT connection already owned by Docking."""

from __future__ import annotations

import ctypes
import threading
import time
from contextlib import ExitStack

from docking.platform.native.windows.com import (
    GUID,
    HRESULT,
    method,
    object_result,
    query,
    value,
)
from docking.platform.native.windows.winrt import WindowsRuntime, check

_DEVICE3 = GUID.parse("aee9e493-44ac-40dc-af33-b2c13c01ca46")
_SERVICE3 = GUID.parse("b293a950-0c53-437c-a9b3-5c3210c6e569")
_BATTERY_SERVICE = GUID.parse("0000180f-0000-1000-8000-00805f9b34fb")
_BATTERY_LEVEL = GUID.parse("00002a19-0000-1000-8000-00805f9b34fb")
_BYTES = GUID.parse("905a0fef-bc53-11df-8c49-001e4fc686da")
_CLOSABLE = GUID.parse("30d5a829-7fa4-4026-83bb-d75bae4ea99e")


def read_battery(
    runtime: WindowsRuntime,
    remote,
    cancelled: threading.Event,
    *,
    timeout: float = 1.5,
) -> int | None:
    deadline = time.monotonic() + timeout

    def complete(scope, pointer, index, *arguments):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or cancelled.is_set():
            raise TimeoutError("Bluetooth battery read timed out")
        operation = object_result(scope, pointer, index, *arguments)
        runtime.wait(scope, operation, cancelled, timeout=remaining)
        result = object_result(scope, operation, 8)
        if value(result, 6, ctypes.c_int32).value != 0:
            raise OSError("Bluetooth battery service is unavailable")
        return result

    try:
        with ExitStack() as scope:
            result = complete(
                scope,
                query(scope, remote, _DEVICE3),
                11,
                GUID,
                _BATTERY_SERVICE,
                ctypes.c_int32,
                0,  # Cached service discovery; never create another BLE device.
            )
            services = object_result(scope, result, 8)
            if value(services, 7, ctypes.c_uint32).value != 1:
                return None  # Do not pick one of several accessory batteries.
            service = object_result(scope, services, 6, ctypes.c_uint32, 0)
            closable = query(scope, service, _CLOSABLE)
            scope.callback(lambda: check(method(closable, 6, HRESULT)(closable)))
            result = complete(
                scope,
                query(scope, service, _SERVICE3),
                14,
                GUID,
                _BATTERY_LEVEL,
                ctypes.c_int32,
                0,
            )
            characteristics = object_result(scope, result, 8)
            if value(characteristics, 7, ctypes.c_uint32).value != 1:
                return None
            characteristic = object_result(
                scope, characteristics, 6, ctypes.c_uint32, 0
            )
            result = complete(
                scope,
                characteristic,
                15,
                ctypes.c_int32,
                1,  # Uncached value.
            )
            buffer = object_result(scope, result, 7)
            if value(buffer, 7, ctypes.c_uint32).value != 1:  # IBuffer.Length
                return None
            access = query(scope, buffer, _BYTES)
            data = ctypes.POINTER(ctypes.c_ubyte)()
            check(
                method(access, 3, HRESULT, ctypes.POINTER(type(data)))(
                    access, ctypes.byref(data)
                )
            )
            return int(data[0]) if data and data[0] <= 100 else None
    except OSError:
        return None  # Optional telemetry cannot turn a connection into failure.
