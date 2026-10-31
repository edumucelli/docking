"""Value-only LE advertisement parsing; pointers belong to the endpoint worker."""

from __future__ import annotations

import ctypes
from contextlib import ExitStack
from dataclasses import dataclass

from docking.platform.backends.windows.com import GUID, object_result, query, value
from docking.platform.backends.windows.winrt import WindowsRuntime

WATCHER_IID = "a6ac336f-f3d3-4297-8d6c-c81ea6623f40"
RECEIVED_IID = "27987ddf-e596-41be-8d43-9e6731d4a913"
_RECEIVED2 = GUID.parse("12d9c87b-0399-5f0e-a348-53b02b6b162e")


@dataclass(frozen=True)
class Advertisement:
    address: int
    address_type: int
    name: str
    rssi: int | None


def read_advertisement(runtime: WindowsRuntime, pointer) -> Advertisement | None:
    with ExitStack() as scope:
        modern = query(scope, pointer, _RECEIVED2)
        if value(modern, 8, ctypes.c_ubyte).value:  # IsAnonymous
            return None
        address = int(value(pointer, 7, ctypes.c_uint64).value)
        address_type = int(value(modern, 6, ctypes.c_int32).value)
        if not 0 < address < 1 << 48 or address_type not in (0, 1):
            return None
        advertisement = object_result(scope, pointer, 10)
        name = runtime.text(advertisement, 8).replace("\0", "")
        strength = int(value(pointer, 6, ctypes.c_int16).value)
        return Advertisement(
            address,
            address_type,
            name,
            strength if -127 <= strength <= 20 else None,
        )
