"""Core Audio capture endpoints and active sessions, scoped to one MTA call.

Capture endpoint IDs, not defaults or friendly names, bind each native request.
Only value objects leave the COM apartment; every interface and returned string
is released on the thread that acquired it.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from pathlib import PureWindowsPath

from docking.applets.micshield.state import MicStream
from docking.i18n import _
from docking.log import get_logger
from docking.platform.backends.windows.com import (
    GUID,
    HRESULT,
    apartment,
    method,
    release,
)
from docking.platform.backends.windows.ffi import bind

log = get_logger("micshield.windows_audio")
_POINTER = ctypes.c_void_p
_ENUMERATOR_CLASS = GUID.parse("bcde0395-e52f-467c-8e3d-c4579291692e")
_ENUMERATOR_INTERFACE = GUID.parse("a95664d2-9614-4f35-a746-de8db63617e6")
_VOLUME = GUID.parse("5cdf2c82-841e-4546-9722-0cf74078229a")
_SESSION_MANAGER = GUID.parse("77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f")
_SESSION_CONTROL = GUID.parse("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")


def _check(status: int) -> None:
    if status < 0:
        raise OSError(f"Core Audio capture failed: 0x{status & 0xFFFFFFFF:08x}")


def _object(stack: ExitStack, pointer, index: int, *arguments):
    value = _POINTER()
    kinds, values = arguments[::2], arguments[1::2]
    try:
        status = method(pointer, index, HRESULT, *kinds, ctypes.POINTER(_POINTER))(
            pointer, *values, ctypes.byref(value)
        )
        _check(status)
        if status != 0 or not value.value:
            raise OSError("Missing Core Audio capture interface")
        return value
    finally:
        if value.value:
            stack.callback(release, value)


def _value(pointer, index, kind):
    value = kind()
    _check(
        method(pointer, index, HRESULT, ctypes.POINTER(kind))(
            pointer, ctypes.byref(value)
        )
    )
    return value.value


def _text(ole, pointer, index: int) -> str:
    value = _POINTER()
    try:
        _check(
            method(pointer, index, HRESULT, ctypes.POINTER(_POINTER))(
                pointer, ctypes.byref(value)
            )
        )
        return ctypes.wstring_at(value) if value.value else ""
    finally:
        if value.value:
            bind(ole, "CoTaskMemFree", None, _POINTER)(value)


def _activate(stack: ExitStack, device, interface: GUID):
    return _object(
        stack,
        device,
        3,
        ctypes.POINTER(GUID),
        ctypes.byref(interface),
        ctypes.c_uint32,
        1,
        _POINTER,
        None,
    )


@dataclass(frozen=True)
class CaptureEndpoint:
    identifier: str
    muted: bool | None
    streams: tuple[MicStream, ...] = ()
    activity_known: bool = True


@contextmanager
def _devices(ole) -> Iterator[tuple[_POINTER, _POINTER]]:
    with ExitStack() as stack:
        enumerator = _POINTER()
        try:
            _check(
                bind(
                    ole,
                    "CoCreateInstance",
                    HRESULT,
                    ctypes.POINTER(GUID),
                    _POINTER,
                    ctypes.c_uint32,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(_POINTER),
                )(
                    ctypes.byref(_ENUMERATOR_CLASS),
                    None,
                    1,
                    ctypes.byref(_ENUMERATOR_INTERFACE),
                    ctypes.byref(enumerator),
                )
            )
        finally:
            if enumerator.value:
                stack.callback(release, enumerator)
        if not enumerator.value:
            raise OSError("Missing capture endpoint enumerator")
        devices = _object(
            stack, enumerator, 3, ctypes.c_int32, 1, ctypes.c_uint32, 1
        )  # eCapture, DEVICE_STATE_ACTIVE
        yield enumerator, devices


def _count(pointer, index: int, kind, maximum: int) -> int:
    count = int(_value(pointer, index, kind))
    if not 0 <= count <= maximum:
        raise OSError("Invalid Core Audio capture count")
    return count


def _process_name(pid: int) -> str:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    process = bind(
        kernel,
        "OpenProcess",
        _POINTER,
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.c_uint32,
    )(0x1000, False, pid)
    if not process:
        return ""
    try:
        text = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint32(len(text))
        if bind(
            kernel,
            "QueryFullProcessImageNameW",
            ctypes.c_int32,
            _POINTER,
            ctypes.c_uint32,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint32),
        )(process, 0, text, ctypes.byref(size)):
            return PureWindowsPath(text.value).stem
        return ""
    finally:
        bind(kernel, "CloseHandle", ctypes.c_int32, _POINTER)(process)


def _stream(ole, stack, control, identity: str) -> MicStream:
    # Metadata failures must not turn a known active capture session into idle.
    name, pid = "", None
    with suppress(OSError):
        name = _text(ole, control, 4)
    try:
        extended = _object(
            stack, control, 0, ctypes.POINTER(GUID), ctypes.byref(_SESSION_CONTROL)
        )
        with suppress(OSError):
            identity = _text(ole, extended, 13) or identity
        value = ctypes.c_uint32()
        status = method(extended, 14, HRESULT, ctypes.POINTER(ctypes.c_uint32))(
            extended, ctypes.byref(value)
        )
        # AUDCLNT_S_NO_SINGLE_PROCESS is successful but not a sole attribution.
        if status == 0 and value.value:
            pid = int(value.value)
    except OSError:
        pass
    process = _process_name(pid) if pid is not None else ""
    command = name if name and not name.startswith("@") else process or _("Unknown")
    return MicStream(identity, command, pid, name)


def _sessions(ole, device, endpoint: str) -> tuple[tuple[MicStream, ...], bool]:
    streams: list[MicStream] = []
    known = True
    with ExitStack() as stack:
        manager = _activate(stack, device, _SESSION_MANAGER)
        sessions = _object(stack, manager, 5)
        count = _count(sessions, 3, ctypes.c_int32, 4096)
        for index in range(count):
            with ExitStack() as item:
                try:
                    control = _object(item, sessions, 4, ctypes.c_int32, index)
                    state = _value(control, 3, ctypes.c_int32)
                    if state not in (0, 1, 2):
                        raise OSError("Unknown capture session state")
                    if state == 1:
                        streams.append(
                            _stream(ole, item, control, f"{endpoint}/session-{index}")
                        )
                except OSError as exc:
                    known = False
                    log.debug("Capture session unavailable: %s", exc)
    return tuple(streams), known


class WindowsCaptureAPI:
    def snapshot(self) -> tuple[CaptureEndpoint, ...]:
        with apartment(multithreaded=True) as ole, _devices(ole) as (_, devices):
            count = _count(devices, 3, ctypes.c_uint32, 256)
            result = []
            for index in range(count):
                with ExitStack() as item:
                    device = _object(item, devices, 4, ctypes.c_uint32, index)
                    identity = _text(ole, device, 5)
                    if not identity:
                        raise OSError("Missing capture endpoint identity")
                    muted = None
                    try:
                        volume = _activate(item, device, _VOLUME)
                        muted = bool(_value(volume, 15, ctypes.c_int32))
                    except OSError as exc:
                        log.debug("Capture mute unavailable: %s", exc)
                    streams, known = (), False
                    try:
                        streams, known = _sessions(ole, device, identity)
                    except OSError as exc:
                        log.debug("Capture activity unavailable: %s", exc)
                    result.append(CaptureEndpoint(identity, muted, streams, known))
            return tuple(result)

    def set_muted(self, muted: bool) -> bool:
        with apartment(multithreaded=True) as ole, _devices(ole) as (_, devices):
            count = _count(devices, 3, ctypes.c_uint32, 256)
            success = count > 0
            for index in range(count):
                with ExitStack() as item:
                    try:
                        device = _object(item, devices, 4, ctypes.c_uint32, index)
                        volume = _activate(item, device, _VOLUME)
                        _check(
                            method(volume, 14, HRESULT, ctypes.c_int32, _POINTER)(
                                volume, muted, None
                            )
                        )
                    except OSError as exc:
                        # Continue protecting other inputs after a partial failure.
                        success = False
                        log.debug("Capture endpoint mute failed: %s", exc)
            return success
