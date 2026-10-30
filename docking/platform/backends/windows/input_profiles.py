"""Documented TSF profile identities and operations on the calling input thread."""

from __future__ import annotations

import ctypes
import uuid
from contextlib import ExitStack
from dataclasses import dataclass

from .com import GUID, HRESULT, create_instance, method, object_result, query
from .ffi import bind
from .winrt import check

KEYBOARD_CATEGORY = "34745c63-b2f0-4784-8b67-5e12c8701a31"
_CLASS = GUID.parse("33c53a50-f456-4884-b049-85fd643ecfed")
_PROFILES = GUID.parse("1f02b6c5-7842-4ee6-8a0b-9a24183a95ca")
_MANAGER = GUID.parse("71c6e74c-0f28-11d8-a82a-00065b84435c")
_P = ctypes.c_void_p


class NativeProfile(ctypes.Structure):
    _fields_ = [
        ("kind", ctypes.c_uint32),
        ("language", ctypes.c_uint16),
        ("clsid", GUID),
        ("profile", GUID),
        ("category", GUID),
        ("substitute", _P),
        ("capabilities", ctypes.c_uint32),
        ("layout", _P),
        ("flags", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class InputProfile:
    kind: int
    language: int
    clsid: str
    identifier: str
    category: str
    substitute: int
    layout: int
    flags: int
    name: str = ""

    @property
    def code(self) -> str:
        if self.kind == 2:
            return f"win32:{self.layout & 0xFFFFFFFF:08x}"
        return f"tsf:{self.language:04x}:{self.clsid}:{self.identifier}"

    @property
    def enabled(self) -> bool:
        return bool(self.flags & 2)


@dataclass(frozen=True)
class InputCatalog:
    profiles: tuple[InputProfile, ...] = ()
    keyboards: tuple[InputProfile, ...] = ()

    def synthetic_layouts(self, loaded: tuple[int, ...]) -> frozenset[str]:
        # A TIP can load an HKL for IMM compatibility without enabling the
        # corresponding plain keyboard. It is not another selectable source.
        languages = {p.language for p in self.profiles}
        known = {p.code for p in self.keyboards}
        enabled = {p.code for p in self.keyboards if p.enabled}
        return frozenset(
            f"win32:{handle & 0xFFFFFFFF:08x}"
            for handle in loaded
            if (
                f"win32:{handle & 0xFFFFFFFF:08x}" in known
                or handle & 0xFFFF in languages
            )
            and f"win32:{handle & 0xFFFFFFFF:08x}" not in enabled
        )


def profile_value(native: NativeProfile, *, name: str = "") -> InputProfile:
    return InputProfile(
        native.kind,
        native.language,
        str(uuid.UUID(bytes_le=bytes(native.clsid))),
        str(uuid.UUID(bytes_le=bytes(native.profile))),
        str(uuid.UUID(bytes_le=bytes(native.category))),
        int(native.substitute or 0),
        int(native.layout or 0),
        native.flags,
        name,
    )


class TextProfiles:
    """Use and release interfaces on the same initialized COM apartment."""

    def __init__(self, scope: ExitStack):
        self._profiles = create_instance(scope, _CLASS, _PROFILES)
        self._manager = query(scope, self._profiles, _MANAGER)
        self._free_string = bind(ctypes.WinDLL("oleaut32"), "SysFreeString", None, _P)
        self._string_length = bind(
            ctypes.WinDLL("oleaut32"), "SysStringLen", ctypes.c_uint32, _P
        )

    def _name(self, native: NativeProfile) -> str:
        text = _P()
        try:
            status = method(
                self._profiles,
                12,
                HRESULT,
                ctypes.POINTER(GUID),
                ctypes.c_uint16,
                ctypes.POINTER(GUID),
                ctypes.POINTER(_P),
            )(
                self._profiles,
                ctypes.byref(native.clsid),
                native.language,
                ctypes.byref(native.profile),
                ctypes.byref(text),
            )
            if status < 0 or not text.value:
                return ""
            length = self._string_length(text)
            if length > 32768:
                raise OSError("Invalid input-profile description")
            return (
                ctypes.string_at(text, length * 2)
                .decode("utf-16-le", errors="replace")
                .replace("\0", "")
            )
        finally:
            if text.value:
                self._free_string(text)

    def profiles(self) -> tuple[InputProfile, ...]:
        entries = []
        with ExitStack() as scope:
            enumerator = object_result(scope, self._manager, 6, ctypes.c_uint16, 0)
            for _ in range(4096):
                native, fetched = NativeProfile(), ctypes.c_uint32()
                status = method(
                    enumerator,
                    4,
                    HRESULT,
                    ctypes.c_uint32,
                    ctypes.POINTER(NativeProfile),
                    ctypes.POINTER(ctypes.c_uint32),
                )(enumerator, 1, ctypes.byref(native), ctypes.byref(fetched))
                check(status)
                if fetched.value == 0:
                    return tuple(entries)
                if fetched.value != 1 or native.kind not in (1, 2):
                    raise OSError("Invalid input-profile enumeration")
                record = profile_value(native)
                if record.kind == 1 and record.category == KEYBOARD_CATEGORY:
                    record = profile_value(native, name=self._name(native))
                entries.append(record)
        raise OSError("Input-profile collection exceeded its limit")

    def is_enabled(self, profile: InputProfile) -> bool:
        if profile.kind != 1:
            raise ValueError("Enabled language-profile queries require a text service")
        value = ctypes.c_int32()
        check(
            method(
                self._profiles,
                18,
                HRESULT,
                ctypes.POINTER(GUID),
                ctypes.c_uint16,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_int32),
            )(
                self._profiles,
                ctypes.byref(GUID.parse(profile.clsid)),
                profile.language,
                ctypes.byref(GUID.parse(profile.identifier)),
                ctypes.byref(value),
            )
        )
        return bool(value.value)

    def for_layout(self, handle: int) -> InputProfile | None:
        native, empty = NativeProfile(), GUID()
        status = method(
            self._manager,
            5,
            HRESULT,
            ctypes.c_uint32,
            ctypes.c_uint16,
            ctypes.POINTER(GUID),
            ctypes.POINTER(GUID),
            _P,
            ctypes.POINTER(NativeProfile),
        )(
            self._manager,
            2,
            handle & 0xFFFF,
            ctypes.byref(empty),
            ctypes.byref(empty),
            handle,
            ctypes.byref(native),
        )
        # A loaded compatibility HKL need not have a registered TSF keyboard.
        if status & 0xFFFFFFFF in (0x80004005, 0x80070057):
            return None
        check(status)
        if status != 0:
            return None
        if native.kind != 2 or (int(native.layout or 0) & 0xFFFFFFFF) != (
            handle & 0xFFFFFFFF
        ):
            raise OSError("Invalid keyboard-profile metadata")
        return profile_value(native)

    def active(self) -> InputProfile | None:
        native = NativeProfile()
        category = GUID.parse(KEYBOARD_CATEGORY)
        status = method(
            self._manager,
            10,
            HRESULT,
            ctypes.POINTER(GUID),
            ctypes.POINTER(NativeProfile),
        )(self._manager, ctypes.byref(category), ctypes.byref(native))
        check(status)
        return profile_value(native) if status == 0 else None
