"""Default Windows output volume through the public Core Audio endpoint API.

Resolve the current multimedia render endpoint for each operation. COM objects
belong to the calling thread and are released before its apartment exits, so
the applet's UI actions and background polls never share an interface pointer.
An absent device does not disable the backend: later polls can recover.
"""

from __future__ import annotations

import ctypes
from collections.abc import Iterator
from contextlib import contextmanager

from docking.applets.volume.backend import VolumeBackend
from docking.applets.volume.state import VolumeState, log
from docking.platform.backends.windows.com import (
    GUID,
    HRESULT,
    apartment,
    method,
    release,
)
from docking.platform.backends.windows.ffi import bind as _bind

_ENUMERATOR_CLASS = GUID.parse("bcde0395-e52f-467c-8e3d-c4579291692e")
_ENUMERATOR_INTERFACE = GUID.parse("a95664d2-9614-4f35-a746-de8db63617e6")
_VOLUME_INTERFACE = GUID.parse("5cdf2c82-841e-4546-9722-0cf74078229a")


def _check(status: int) -> None:
    if status < 0:
        raise OSError(f"Core Audio failed: 0x{status & 0xFFFFFFFF:08x}")


@contextmanager
def _endpoint() -> Iterator[ctypes.c_void_p]:
    with apartment() as ole:
        enumerator, device, volume = (ctypes.c_void_p() for _ in range(3))
        try:
            create = _bind(
                ole,
                "CoCreateInstance",
                HRESULT,
                ctypes.POINTER(GUID),
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            )
            _check(
                create(
                    ctypes.byref(_ENUMERATOR_CLASS),
                    None,
                    1,  # CLSCTX_INPROC_SERVER
                    ctypes.byref(_ENUMERATOR_INTERFACE),
                    ctypes.byref(enumerator),
                )
            )
            default = method(
                enumerator,
                4,  # IMMDeviceEnumerator::GetDefaultAudioEndpoint
                HRESULT,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.POINTER(ctypes.c_void_p),
            )
            _check(
                default(enumerator, 0, 1, ctypes.byref(device))
            )  # render, multimedia
            activate = method(
                device,
                3,  # IMMDevice::Activate
                HRESULT,
                ctypes.POINTER(GUID),
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            )
            _check(
                activate(
                    device,
                    ctypes.byref(_VOLUME_INTERFACE),
                    1,
                    None,
                    ctypes.byref(volume),
                )
            )
            yield volume
        finally:
            for pointer in (volume, device, enumerator):
                release(pointer)


def _muted(endpoint: ctypes.c_void_p) -> bool:
    muted = ctypes.c_int32()
    get = method(endpoint, 15, HRESULT, ctypes.POINTER(ctypes.c_int32))
    _check(get(endpoint, ctypes.byref(muted)))
    return bool(muted.value)


def get_state() -> VolumeState | None:
    try:
        with _endpoint() as endpoint:
            scalar = ctypes.c_float()
            get = method(endpoint, 9, HRESULT, ctypes.POINTER(ctypes.c_float))
            _check(get(endpoint, ctypes.byref(scalar)))
            percent = max(0, min(100, int(scalar.value * 100 + 0.5)))
            return VolumeState(percent, _muted(endpoint))
    except OSError as exc:
        log.debug("Could not read Windows output volume: %s", exc)
        return None


def set_volume(volume: int) -> None:
    try:
        with _endpoint() as endpoint:
            set_level = method(endpoint, 7, HRESULT, ctypes.c_float, ctypes.c_void_p)
            _check(set_level(endpoint, max(0, min(100, volume)) / 100, None))
    except OSError as exc:
        log.warning("Could not set Windows output volume: %s", exc)


def toggle_mute() -> None:
    try:
        with _endpoint() as endpoint:
            muted = _muted(endpoint)
            set_mute = method(endpoint, 14, HRESULT, ctypes.c_int32, ctypes.c_void_p)
            _check(set_mute(endpoint, not muted, None))
    except OSError as exc:
        log.warning("Could not toggle Windows output mute: %s", exc)


def create_backend() -> VolumeBackend:
    return VolumeBackend("Core Audio", get_state, set_volume, toggle_mute)
