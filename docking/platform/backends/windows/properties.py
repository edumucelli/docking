"""Read explicit application identity from a native window property store."""

from __future__ import annotations

import ctypes

from .com import GUID, HRESULT, apartment, method, release
from .ffi import bind


class PropertyKey(ctypes.Structure):
    _fields_ = (("format", GUID), ("id", ctypes.c_uint32))


class _Value(ctypes.Union):
    _fields_ = (("string", ctypes.c_wchar_p), ("record", ctypes.c_void_p * 2))


class PropertyVariant(ctypes.Structure):
    _fields_ = (
        ("vt", ctypes.c_uint16),
        ("reserved", ctypes.c_uint16 * 3),
        ("data", _Value),
    )


APP_ID = PropertyKey(GUID.parse("9f4c2855-9f79-4b39-a8d0-e1d42de1d5f3"), 5)
PROPERTY_STORE = GUID.parse("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99")


class WindowsWindowProperties:
    """Each read owns its apartment, property store and returned PROPVARIANT."""

    def __init__(self) -> None:
        self._store = bind(
            ctypes.WinDLL("shell32", use_last_error=True),
            "SHGetPropertyStoreForWindow",
            HRESULT,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )

    def app_id(self, window: int) -> str | None:
        with apartment() as ole:
            store, value = ctypes.c_void_p(), PropertyVariant()
            try:
                status = self._store(
                    window, ctypes.byref(PROPERTY_STORE), ctypes.byref(store)
                )
                if status < 0 or not store.value:
                    return None
                status = method(
                    store,
                    5,
                    HRESULT,
                    ctypes.POINTER(PropertyKey),
                    ctypes.POINTER(PropertyVariant),
                )(store, ctypes.byref(APP_ID), ctypes.byref(value))
                if status < 0 or value.vt != 31:  # VT_LPWSTR; VT_EMPTY is unset.
                    return None
                result = value.data.string
                return str(result) if result and len(result) <= 128 else None
            finally:
                try:
                    status = bind(
                        ole,
                        "PropVariantClear",
                        HRESULT,
                        ctypes.POINTER(PropertyVariant),
                    )(ctypes.byref(value))
                    if status < 0:
                        raise OSError(
                            "Windows did not release the window property value"
                        )
                finally:
                    release(store)
