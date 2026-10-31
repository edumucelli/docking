"""Machine-wide camera recovery data, writable only by administrators/SYSTEM.

The protected registry key avoids user-selected paths and filesystem links in
the elevated helper. Every read checks its owner and DACL before trusting data.
"""

from __future__ import annotations

import ctypes
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from docking.platform.backends.windows.ffi import bind

KEY = r"SOFTWARE\DockingCamShieldRecovery"
_SDDL = "O:BAD:P(A;;KA;;;SY)(A;;KA;;;BA)(A;;KR;;;BU)"
_P = ctypes.c_void_p
_U32 = ctypes.c_uint32


@dataclass(frozen=True)
class RecordedPolicy:
    value: int | None


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [("size", _U32), ("descriptor", _P), ("inherit", ctypes.c_int32)]


def _free(pointer) -> None:
    if pointer.value:
        bind(ctypes.WinDLL("kernel32"), "LocalFree", _P, _P)(pointer)


def _descriptor(advapi):
    pointer = _P()
    if not bind(
        advapi,
        "ConvertStringSecurityDescriptorToSecurityDescriptorW",
        ctypes.c_int32,
        ctypes.c_wchar_p,
        _U32,
        ctypes.POINTER(_P),
        _P,
    )(_SDDL, 1, ctypes.byref(pointer), None):
        raise ctypes.WinError(ctypes.get_last_error())
    return pointer


def _canonical(advapi, descriptor) -> str:
    text = _P()
    try:
        if not bind(
            advapi,
            "ConvertSecurityDescriptorToStringSecurityDescriptorW",
            ctypes.c_int32,
            _P,
            _U32,
            _U32,
            ctypes.POINTER(_P),
            _P,
        )(descriptor, 1, 5, ctypes.byref(text), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if not text.value:
            raise OSError("Missing camera recovery security descriptor")
        return ctypes.wstring_at(text)
    finally:
        _free(text)


def _verify(advapi, handle: int, expected) -> None:
    descriptor = _P()
    try:
        status = bind(
            advapi,
            "GetSecurityInfo",
            _U32,
            _P,
            ctypes.c_int,
            _U32,
            _P,
            _P,
            _P,
            _P,
            ctypes.POINTER(_P),
        )(handle, 4, 5, None, None, None, None, ctypes.byref(descriptor))
        if status:
            raise ctypes.WinError(status)
        if not descriptor.value or _canonical(advapi, descriptor) != _canonical(
            advapi, expected
        ):
            raise PermissionError(
                "Camera recovery registry ownership or permissions changed"
            )
    finally:
        _free(descriptor)


@contextmanager
def _open(*, create: bool) -> Iterator[int | None]:
    import winreg

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    descriptor = _descriptor(advapi)
    handle = None
    try:
        if create:
            attributes = _SecurityAttributes(
                ctypes.sizeof(_SecurityAttributes), descriptor, 0
            )
            result, disposition = _P(), _U32()
            status = bind(
                advapi,
                "RegCreateKeyExW",
                _U32,
                _P,
                ctypes.c_wchar_p,
                _U32,
                _P,
                _U32,
                _U32,
                ctypes.POINTER(_SecurityAttributes),
                ctypes.POINTER(_P),
                ctypes.POINTER(_U32),
            )(
                int(winreg.HKEY_LOCAL_MACHINE),
                KEY,
                0,
                None,
                0,
                0xF003F | 0x100,
                ctypes.byref(attributes),
                ctypes.byref(result),
                ctypes.byref(disposition),
            )
            if status:
                raise ctypes.WinError(status)
            handle = result.value
        else:
            try:
                handle = int(
                    winreg.OpenKey(
                        winreg.HKEY_LOCAL_MACHINE,
                        KEY,
                        0,
                        winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                    ).Detach()
                )
            except FileNotFoundError:
                yield None
                return
        if handle is None:
            raise OSError("Missing camera recovery registry handle")
        _verify(advapi, handle, descriptor)
        yield handle
    finally:
        if handle is not None:
            winreg.CloseKey(handle)
        _free(descriptor)


def _decode(text: str) -> RecordedPolicy | None:
    if len(text) > 4096:
        raise OSError("Camera recovery record is too large")
    try:
        value = json.loads(text)
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value["version"] != 1
        ):
            raise ValueError("Invalid camera recovery version")
        if set(value) != {"version", "policy"}:
            raise ValueError("Invalid camera recovery fields")
        policy = value["policy"]
        if policy is None:
            return None
        if not isinstance(policy, dict) or set(policy) != {"original"}:
            raise ValueError("Invalid camera recovery policy")
        original = policy["original"]
        if original is not None and (type(original) is not int or original != 1):
            raise ValueError("Invalid original camera policy")
        return RecordedPolicy(original)
    except (ValueError, TypeError) as exc:
        raise OSError("Invalid camera recovery data") from exc


class WindowsCameraJournal:
    def read(self) -> RecordedPolicy | None:
        import winreg

        with _open(create=False) as handle:
            if handle is None:
                return None
            try:
                value, kind = winreg.QueryValueEx(handle, "State")
            except FileNotFoundError:
                return None
            if kind != winreg.REG_SZ or not isinstance(value, str):
                raise OSError("Invalid camera recovery registry value")
            return _decode(value)

    def write(self, record: RecordedPolicy | None) -> None:
        import winreg

        text = json.dumps(
            {
                "version": 1,
                "policy": {"original": record.value} if record is not None else None,
            },
            ensure_ascii=False,
        )
        _decode(text)
        with _open(create=True) as handle:
            assert handle is not None
            winreg.SetValueEx(handle, "State", 0, winreg.REG_SZ, text)
            winreg.FlushKey(handle)  # Recovery intent is durable before blocking.
