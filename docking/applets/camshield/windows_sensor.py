"""Passive Media Foundation camera inventory and sensor activity reports.

This module runs in an owned helper process. It never activates a media source
or requests camera frames. Native callback pointers remain alive until monitor
stop, release and Media Foundation shutdown have completed.
"""

from __future__ import annotations

import ctypes
import threading
from contextlib import ExitStack
from typing import Any

from docking.platform.backends.windows.com import (
    GUID,
    HRESULT,
    method,
    release,
)
from docking.platform.backends.windows.ffi import bind

_P = ctypes.c_void_p
_U32 = ctypes.c_uint32
_CALLBACK = "de5072ee-dbe3-46dc-8a87-b6f631194751"
_UNKNOWN = "00000000-0000-0000-c000-000000000046"
_SOURCE_TYPE = GUID.parse("c60ac5fe-252a-478f-a0ef-bc8fa5f7cad3")
_VIDEO_SOURCE = GUID.parse("8ac3587a-4ae7-42d8-99e0-0a6013eef90f")
_FRIENDLY_NAME = GUID.parse("60d0e559-52f8-4fa2-bbce-acdb34a8ec01")
_SYMBOLIC_LINK = GUID.parse("58f0aad8-22bf-4f8a-bb3d-d2c4978c6e2f")


def _check(status: int) -> None:
    if status < 0:
        raise OSError(f"Camera sensor API failed: 0x{status & 0xFFFFFFFF:08x}")


def _value(pointer, index: int) -> int:
    result = _U32()
    _check(
        method(pointer, index, HRESULT, ctypes.POINTER(_U32))(
            pointer, ctypes.byref(result)
        )
    )
    return int(result.value)


def _object(stack: ExitStack, pointer, index: int, position: int):
    result = _P()
    try:
        _check(
            method(pointer, index, HRESULT, _U32, ctypes.POINTER(_P))(
                pointer, position, ctypes.byref(result)
            )
        )
        if not result.value:
            raise OSError("Missing camera report")
        return result
    finally:
        if result.value:
            stack.callback(release, result)


def _report_text(pointer, index: int) -> str:
    text = ctypes.create_unicode_buffer(32768)
    length = _U32()
    _check(
        method(pointer, index, HRESULT, ctypes.c_wchar_p, _U32, ctypes.POINTER(_U32))(
            pointer, text, len(text), ctypes.byref(length)
        )
    )
    return str(text.value)


def _attribute_text(ole, pointer, key: GUID) -> str:
    value, length = _P(), _U32()
    try:
        _check(
            method(
                pointer,
                13,
                HRESULT,
                ctypes.POINTER(GUID),
                ctypes.POINTER(_P),
                ctypes.POINTER(_U32),
            )(pointer, ctypes.byref(key), ctypes.byref(value), ctypes.byref(length))
        )
        if length.value > 32768 or (length.value and not value.value):
            raise OSError("Invalid camera attribute string")
        return ctypes.wstring_at(value, length.value) if value.value else ""
    finally:
        if value.value:
            bind(ole, "CoTaskMemFree", None, _P)(value)


def inventory(ole) -> list[dict[str, Any]]:
    """Enumerate video capture source metadata without opening any source."""
    platform = ctypes.WinDLL("mfplat")
    media = ctypes.WinDLL("mf")
    attributes = _P()
    array = ctypes.POINTER(_P)()
    count = _U32()
    try:
        _check(
            bind(platform, "MFCreateAttributes", HRESULT, ctypes.POINTER(_P), _U32)(
                ctypes.byref(attributes), 1
            )
        )
        if not attributes.value:
            raise OSError("Missing camera inventory attributes")
        _check(
            method(attributes, 24, HRESULT, ctypes.POINTER(GUID), ctypes.POINTER(GUID))(
                attributes, ctypes.byref(_SOURCE_TYPE), ctypes.byref(_VIDEO_SOURCE)
            )
        )
        _check(
            bind(
                media,
                "MFEnumDeviceSources",
                HRESULT,
                _P,
                ctypes.POINTER(ctypes.POINTER(_P)),
                ctypes.POINTER(_U32),
            )(attributes, ctypes.byref(array), ctypes.byref(count))
        )
        if count.value > 256 or (count.value and not array):
            raise OSError("Invalid camera inventory count")
        result = []
        for i in range(count.value):
            device = _P(array[i])
            if not device.value:
                raise OSError("Missing camera inventory device")
            identity = _attribute_text(ole, device, _SYMBOLIC_LINK)
            name = _attribute_text(ole, device, _FRIENDLY_NAME)
            if not identity:
                raise OSError("Missing camera identity")
            result.append({"id": identity, "name": name or identity})
        return result
    finally:
        if array:
            if count.value <= 256:
                for i in range(count.value):
                    release(_P(array[i]))
            bind(ole, "CoTaskMemFree", None, _P)(array)
        release(attributes)


class _Instance(ctypes.Structure):
    _fields_ = [("table", ctypes.POINTER(_P))]


class SensorMonitor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = False
        self._references = 1
        self._known = False
        self._reports: dict[str, dict[str, Any]] = {}
        self._monitor = _P()
        # These thunks and the native instance stay owned by this object for
        # the entire monitor lifetime, including Stop/Release callbacks.
        callback = ctypes.WINFUNCTYPE
        self._functions = (
            callback(HRESULT, _P, ctypes.POINTER(GUID), ctypes.POINTER(_P))(
                self._query
            ),
            callback(_U32, _P)(self._addref),
            callback(_U32, _P)(self._release),
            callback(HRESULT, _P, _P)(self._report),
        )
        self._table = (_P * 4)(*(ctypes.cast(f, _P).value for f in self._functions))
        self._instance = _Instance(self._table)

    def _query(self, _this, iid, output):
        if not output or not iid:
            return -2147467261  # E_POINTER
        output[0] = None
        if bytes(iid.contents) not in (
            bytes(GUID.parse(_CALLBACK)),
            bytes(GUID.parse(_UNKNOWN)),
        ):
            return -2147467262  # E_NOINTERFACE
        output[0] = ctypes.addressof(self._instance)
        self._addref(_this)
        return 0

    def _addref(self, _this):
        with self._lock:
            self._references += 1
            return self._references

    def _release(self, _this):
        with self._lock:
            self._references -= 1
            return self._references

    def _report(self, _this, pointer):
        try:
            values = []
            count = _value(pointer, 3)
            if count > 256:
                raise OSError("Invalid camera activity count")
            for i in range(count):
                with ExitStack() as item:
                    report = _object(item, pointer, 4, i)
                    identity = _report_text(report, 4)
                    name = _report_text(report, 3)
                    if not identity:
                        raise OSError("Missing camera report identity")
                    processes = []
                    occupied = _value(report, 5)
                    if occupied > 4096:
                        raise OSError("Invalid camera process count")
                    for index in range(occupied):
                        with ExitStack() as entry:
                            process = _object(entry, report, 6, index)
                            pid = _value(process, 3)
                            if not pid:
                                raise OSError("Missing camera process identity")
                            streaming = _value(process, 4)
                            processes.append({"pid": pid, "streaming": bool(streaming)})
                    values.append(
                        {"id": identity, "name": name, "processes": processes}
                    )
            with self._lock:
                if not self._closed:
                    # Retain only this notification. If it omits a currently
                    # enumerated camera, the client reports unknown activity
                    # rather than keeping a stale active/idle report.
                    self._reports = {v["id"].casefold(): v for v in values}
                    self._known = True
        except Exception:
            with self._lock:
                self._known = False
                self._reports.clear()
        self._ready.set()
        return 0

    def start(self) -> None:
        dll = ctypes.WinDLL("mfsensorgroup")
        try:
            _check(
                bind(
                    dll,
                    "MFCreateSensorActivityMonitor",
                    HRESULT,
                    _P,
                    ctypes.POINTER(_P),
                )(ctypes.byref(self._instance), ctypes.byref(self._monitor))
            )
            if not self._monitor.value:
                raise OSError("Missing camera activity monitor")
            _check(method(self._monitor, 3, HRESULT)(self._monitor))
            self._ready.wait(1)
        except Exception:
            self.stop()
            raise

    def snapshot(self) -> tuple[bool, list[dict[str, Any]]]:
        with self._lock:
            return self._known, [*self._reports.values()]

    def stop(self) -> None:
        with self._lock:
            self._closed = True
        try:
            if self._monitor.value:
                method(self._monitor, 4, HRESULT)(self._monitor)
        finally:
            release(self._monitor)
            self._monitor = _P()
