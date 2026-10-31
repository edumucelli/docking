"""Windows connection metadata without location-sensitive WLAN queries.

Use WinRT GetConnectedSsid/GetSignalBars, as recommended by Microsoft's Wi-Fi
location guidance. All HSTRINGs and interface pointers belong to one call and
are released on its thread; only DLL bindings are cached.
"""

from __future__ import annotations

import ctypes
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from functools import lru_cache

from docking.platform.backends.windows.com import GUID, HRESULT, method, release
from docking.platform.backends.windows.ffi import bind

_STATICS = GUID.parse("5074f851-950d-4165-9c15-365619481eea")
_PROFILE2 = GUID.parse("e2045145-4c9f-400c-9150-7ec7d6e2888a")


@dataclass(frozen=True)
class ConnectionProfile:
    identifier: str
    is_wifi: bool
    ssid: str = ""
    strength: int | None = None


def _check(result: int) -> None:
    if result < 0:
        raise OSError(f"Network profile query failed: 0x{result & 0xFFFFFFFF:08x}")


def _value(pointer, index, kind):
    if not pointer:
        raise OSError("Missing network profile interface")
    value = kind()
    _check(
        method(pointer, index, HRESULT, ctypes.POINTER(kind))(
            pointer, ctypes.byref(value)
        )
    )
    return value


def _object(stack: ExitStack, pointer, index: int, *arguments):
    if not pointer:
        raise OSError("Missing network profile interface")
    value = ctypes.c_void_p()
    kinds, values = arguments[::2], arguments[1::2]
    try:
        _check(
            method(pointer, index, HRESULT, *kinds, ctypes.POINTER(ctypes.c_void_p))(
                pointer, *values, ctypes.byref(value)
            )
        )
    finally:
        if value.value:
            stack.callback(release, value)
    return value


class _ProfileAPI:
    def __init__(self):
        dll = ctypes.WinDLL("combase", use_last_error=True)
        self._initialize = bind(dll, "RoInitialize", HRESULT, ctypes.c_uint32)
        self._uninitialize = bind(dll, "RoUninitialize", None)
        self._create_string = bind(
            dll,
            "WindowsCreateString",
            HRESULT,
            ctypes.POINTER(ctypes.c_uint16),
            ctypes.c_uint32,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._delete_string = bind(dll, "WindowsDeleteString", HRESULT, ctypes.c_void_p)
        self._raw_string = bind(
            dll,
            "WindowsGetStringRawBuffer",
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        self._factory = bind(
            dll,
            "RoGetActivationFactory",
            HRESULT,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )

    @contextmanager
    def _apartment(self):
        result = self._initialize(1)
        if result < 0 and result & 0xFFFFFFFF != 0x80010106:
            _check(result)
        try:
            yield
        finally:
            if result in (0, 1):
                self._uninitialize()

    @contextmanager
    def _string(self, text: str):
        data = text.encode("utf-16-le")
        buffer = (ctypes.c_uint16 * (len(data) // 2 + 1)).from_buffer_copy(
            data + b"\0\0"
        )
        value = ctypes.c_void_p()
        try:
            _check(self._create_string(buffer, len(data) // 2, ctypes.byref(value)))
            yield value
        finally:
            if value.value:
                self._delete_string(value)

    def _text(self, pointer, index):
        value = ctypes.c_void_p()
        try:
            _check(
                method(pointer, index, HRESULT, ctypes.POINTER(ctypes.c_void_p))(
                    pointer, ctypes.byref(value)
                )
            )
            length = ctypes.c_uint32()
            raw = self._raw_string(value, ctypes.byref(length))
            if length.value > 32768 or (length.value and not raw):
                raise OSError("Invalid network profile string")
            return (
                ctypes.string_at(raw, length.value * 2).decode(
                    "utf-16-le", errors="replace"
                )
                if length.value
                else ""
            )
        finally:
            if value.value:
                self._delete_string(value)

    def profiles(self) -> list[ConnectionProfile]:
        with (
            self._apartment(),
            ExitStack() as stack,
            self._string("Windows.Networking.Connectivity.NetworkInformation") as name,
        ):
            factory = ctypes.c_void_p()
            try:
                _check(
                    self._factory(name, ctypes.byref(_STATICS), ctypes.byref(factory))
                )
            finally:
                if factory.value:
                    stack.callback(release, factory)
            profiles = _object(stack, factory, 6)
            if not profiles.value:
                return []
            count = _value(profiles, 7, ctypes.c_uint32).value
            if count > 4096:
                raise OSError("Invalid connection profile count")
            result = []
            for index in range(count):
                # Bound references per item, including after an adapter disappears.
                with ExitStack() as item:
                    try:
                        profile = _object(item, profiles, 6, ctypes.c_uint32, index)
                        value = self._profile(item, profile)
                    except OSError:
                        continue
                    if value is not None:
                        result.append(value)
            return result

    def _profile(self, stack: ExitStack, profile) -> ConnectionProfile | None:
        if not _value(profile, 7, ctypes.c_int32).value:
            return None
        adapter = _object(stack, profile, 11)
        if not adapter.value:
            return None
        identifier = (
            "{" + str(uuid.UUID(bytes_le=bytes(_value(adapter, 10, GUID)))) + "}"
        )
        extended = _object(
            stack, profile, 0, ctypes.POINTER(GUID), ctypes.byref(_PROFILE2)
        )
        if not _value(extended, 7, ctypes.c_ubyte).value:
            return ConnectionProfile(identifier, False)
        details = _object(stack, extended, 9)
        ssid = self._text(details, 6) if details.value else ""
        strength = None
        try:
            bars = _object(stack, extended, 11)
            if bars.value:
                value = _value(bars, 6, ctypes.c_ubyte).value
                if value <= 5:
                    strength = value * 20
        except OSError:
            pass
        return ConnectionProfile(identifier, True, ssid, strength)


@lru_cache(maxsize=1)
def _profile_api() -> _ProfileAPI:
    return _ProfileAPI()


def read_connections() -> list[ConnectionProfile]:
    return _profile_api().profiles()
