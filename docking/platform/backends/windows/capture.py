"""Worker-owned Windows.Graphics.Capture and Direct3D11 frame readback.

The capture item targets an HWND, including occluded application pixels. COM
interfaces, GPU textures and mapped memory remain on the owning worker thread;
only copied RGBA pixels may cross into GTK. No foreign application paint call
or desktop screenshot is used. See docs/WINDOWS.md for the native API sources.
"""

from __future__ import annotations

import ctypes
import time
from contextlib import ExitStack
from ctypes import wintypes
from dataclasses import dataclass

from .com import GUID, HRESULT, method, release
from .native import _bind

_POINTER = ctypes.c_void_p
_OUT = ctypes.POINTER(_POINTER)


class _Size(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int32), ("height", ctypes.c_int32)]


class _Sample(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("quality", ctypes.c_uint32)]


class _TextureDescription(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("mips", ctypes.c_uint32),
        ("array_size", ctypes.c_uint32),
        ("format", ctypes.c_uint32),
        ("sample", _Sample),
        ("usage", ctypes.c_uint32),
        ("bind", ctypes.c_uint32),
        ("cpu_access", ctypes.c_uint32),
        ("misc", ctypes.c_uint32),
    ]


class _Mapped(ctypes.Structure):
    _fields_ = [
        ("data", _POINTER),
        ("row_pitch", ctypes.c_uint32),
        ("depth_pitch", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class CapturePixels:
    pixels: bytes
    width: int
    height: int


def rgba_pixels(data: bytes, width: int, height: int, stride: int) -> CapturePixels:
    """Copy BGRA rows and remove premultiplication without retaining GPU memory."""
    if not (
        0 < width <= 16384
        and 0 < height <= 16384
        and width * 4 <= stride <= 262144
        and stride * height <= 128 * 1024 * 1024
        and len(data) >= stride * height
    ):
        raise ValueError("Invalid Windows capture buffer")
    import numpy as np

    rows = np.frombuffer(data, dtype=np.uint8, count=stride * height).reshape(
        height, stride
    )
    bgra = rows[:, : width * 4].reshape(height, width, 4)
    rgba = bgra[:, :, [2, 1, 0, 3]].copy()
    alpha = rgba[:, :, 3:4].astype(np.uint16)
    rgba[:, :, :3] = np.minimum(
        255, rgba[:, :, :3].astype(np.uint16) * 255 // np.maximum(alpha, 1)
    ).astype(np.uint8)
    rgba[:, :, :3][rgba[:, :, 3] == 0] = 0
    return CapturePixels(rgba.tobytes(), width, height)


def _check(status: int, operation: str) -> None:
    if status < 0:
        raise OSError(f"{operation} failed: 0x{status & 0xFFFFFFFF:08x}")


def _query(pointer: _POINTER, iid: str) -> _POINTER:
    result = _POINTER()
    guid = GUID.parse(iid)
    _check(
        method(pointer, 0, HRESULT, ctypes.POINTER(GUID), _OUT)(
            pointer, ctypes.byref(guid), ctypes.byref(result)
        ),
        "QueryInterface",
    )
    return result


def _close(pointer: _POINTER) -> None:
    closable = _query(pointer, "30d5a829-7fa4-4026-83bb-d75bae4ea99e")
    try:
        method(closable, 6, HRESULT)(closable)
    finally:
        release(closable)


class WindowsCaptureDevice:
    """Create and destroy on the same MTA worker, after its streams close."""

    def __init__(self) -> None:
        self._resources = ExitStack()
        self._closed = False
        self.device = _POINTER()
        self.context = _POINTER()
        self.runtime_device = _POINTER()
        self._runtime = ctypes.WinDLL("combase", use_last_error=True)
        try:
            _check(
                _bind(self._runtime, "RoInitialize", HRESULT, ctypes.c_uint32)(1),
                "RoInitialize",
            )
            self._resources.callback(_bind(self._runtime, "RoUninitialize", None))
            supported = self.factory(
                "Windows.Graphics.Capture.GraphicsCaptureSession",
                "2224a540-5974-49aa-b232-0882536f4cb5",
            )
            try:
                enabled = ctypes.c_ubyte()
                _check(
                    method(supported, 6, HRESULT, ctypes.POINTER(ctypes.c_ubyte))(
                        supported, ctypes.byref(enabled)
                    ),
                    "GraphicsCaptureSession.IsSupported",
                )
                if not enabled.value:
                    raise OSError(
                        "Windows graphics capture is unsupported on this display"
                    )
            finally:
                release(supported)
            d3d = ctypes.WinDLL("d3d11", use_last_error=True)
            create = _bind(
                d3d,
                "D3D11CreateDevice",
                HRESULT,
                _POINTER,
                ctypes.c_uint32,
                _POINTER,
                ctypes.c_uint32,
                _POINTER,
                ctypes.c_uint32,
                ctypes.c_uint32,
                _OUT,
                _POINTER,
                _OUT,
            )
            status = create(
                None,
                1,
                None,
                0x20,
                None,
                0,
                7,
                ctypes.byref(self.device),
                None,
                ctypes.byref(self.context),
            )
            if status < 0:
                status = create(
                    None,
                    5,
                    None,
                    0x20,
                    None,
                    0,
                    7,
                    ctypes.byref(self.device),
                    None,
                    ctypes.byref(self.context),
                )
            _check(status, "D3D11CreateDevice")
            self._resources.callback(release, self.device)
            self._resources.callback(release, self.context)
            dxgi = _query(self.device, "54ec77fa-1377-44e6-8c32-88fd5f44c84c")
            inspectable = _POINTER()
            try:
                _check(
                    _bind(
                        d3d,
                        "CreateDirect3D11DeviceFromDXGIDevice",
                        HRESULT,
                        _POINTER,
                        _OUT,
                    )(dxgi, ctypes.byref(inspectable)),
                    "CreateDirect3D11DeviceFromDXGIDevice",
                )
                self.runtime_device = _query(
                    inspectable, "a37624ab-8d5f-4650-9d3e-9eae3d9bc670"
                )
                self._resources.callback(release, self.runtime_device)
            finally:
                release(inspectable)
                release(dxgi)
            user = ctypes.WinDLL("user32", use_last_error=True)
            self._pid = _bind(
                user,
                "GetWindowThreadProcessId",
                wintypes.DWORD,
                wintypes.HWND,
                ctypes.POINTER(wintypes.DWORD),
            )
        except BaseException:
            self._resources.close()
            raise

    def factory(self, name: str, iid: str) -> _POINTER:
        string, result = _POINTER(), _POINTER()
        guid = GUID.parse(iid)
        _check(
            _bind(
                self._runtime,
                "WindowsCreateString",
                HRESULT,
                wintypes.LPCWSTR,
                ctypes.c_uint32,
                _OUT,
            )(name, len(name), ctypes.byref(string)),
            "WindowsCreateString",
        )
        try:
            _check(
                _bind(
                    self._runtime,
                    "RoGetActivationFactory",
                    HRESULT,
                    _POINTER,
                    ctypes.POINTER(GUID),
                    _OUT,
                )(string, ctypes.byref(guid), ctypes.byref(result)),
                "RoGetActivationFactory",
            )
            return result
        finally:
            _bind(self._runtime, "WindowsDeleteString", HRESULT, _POINTER)(string)

    def current_window(self, hwnd: int, pid: int) -> bool:
        current = wintypes.DWORD()
        return bool(self._pid(hwnd, ctypes.byref(current)) and current.value == pid)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._resources.close()


class WindowsCaptureStream:
    """A free-threaded frame pool and one window; all methods are worker-only."""

    def __init__(self, device: WindowsCaptureDevice, hwnd: int, pid: int) -> None:
        self._device = device
        self.hwnd, self.pid = hwnd, pid
        self._resources = ExitStack()
        self._closed = False
        self._staging = _POINTER()
        self._texture_size = (0, 0)
        self._resize_pending = False
        self.pool = _POINTER()
        self.session = _POINTER()
        try:
            if not device.current_window(hwnd, pid):
                raise OSError("Capture source window no longer belongs to this process")
            interop = device.factory(
                "Windows.Graphics.Capture.GraphicsCaptureItem",
                "3628e81b-3cac-4c60-b7f4-23ce0e0c3356",
            )
            item = _POINTER()
            guid = GUID.parse("79c3f95b-31f7-4ec2-a464-632ef5d30760")
            try:
                _check(
                    method(
                        interop, 3, HRESULT, wintypes.HWND, ctypes.POINTER(GUID), _OUT
                    )(interop, hwnd, ctypes.byref(guid), ctypes.byref(item)),
                    "CreateForWindow",
                )
            finally:
                release(interop)
            self._resources.callback(release, item)
            self._size = size = _Size()
            _check(
                method(item, 7, HRESULT, ctypes.POINTER(_Size))(
                    item, ctypes.byref(size)
                ),
                "GraphicsCaptureItem.Size",
            )
            self._validate_size(size)
            factory = device.factory(
                "Windows.Graphics.Capture.Direct3D11CaptureFramePool",
                "589b103f-6bbc-5df5-a991-02e28b3b66d5",
            )
            try:
                _check(
                    method(
                        factory,
                        6,
                        HRESULT,
                        _POINTER,
                        ctypes.c_int32,
                        ctypes.c_int32,
                        _Size,
                        _OUT,
                    )(
                        factory,
                        device.runtime_device,
                        87,
                        2,
                        size,
                        ctypes.byref(self.pool),
                    ),
                    "CreateFreeThreaded",
                )
            finally:
                release(factory)
            self._resources.callback(release, self.pool)
            self._resources.callback(_close, self.pool)
            _check(
                method(self.pool, 10, HRESULT, _POINTER, _OUT)(
                    self.pool, item, ctypes.byref(self.session)
                ),
                "CreateCaptureSession",
            )
            self._resources.callback(release, self.session)
            self._resources.callback(_close, self.session)
            cursor = _query(self.session, "2c39ae40-7d2e-5044-804e-8b6799d4cf9e")
            try:
                _check(
                    method(cursor, 7, HRESULT, ctypes.c_ubyte)(cursor, 0),
                    "Disable capture cursor",
                )
            finally:
                release(cursor)
            _check(method(self.session, 6, HRESULT)(self.session), "StartCapture")
        except BaseException:
            self._resources.close()
            raise

    @staticmethod
    def _validate_size(size: _Size) -> None:
        if not (
            0 < size.width <= 16384
            and 0 < size.height <= 16384
            and size.width * size.height * 4 <= 128 * 1024 * 1024
        ):
            raise ValueError("Unsupported Windows capture dimensions")

    def read(self, timeout: float = 0.3) -> CapturePixels | None:
        if self._closed or not self._device.current_window(self.hwnd, self.pid):
            return None
        if self._resize_pending:
            _check(
                method(
                    self.pool,
                    6,
                    HRESULT,
                    _POINTER,
                    ctypes.c_int32,
                    ctypes.c_int32,
                    _Size,
                )(self.pool, self._device.runtime_device, 87, 2, self._size),
                "Recreate frame pool",
            )
            self._resize_pending = False
        deadline = time.monotonic() + timeout
        while True:
            frame = _POINTER()
            _check(
                method(self.pool, 7, HRESULT, _OUT)(self.pool, ctypes.byref(frame)),
                "TryGetNextFrame",
            )
            if frame.value:
                try:
                    result = self._read_frame(frame)
                finally:
                    try:
                        _close(frame)
                    finally:
                        release(frame)
                if self._device.current_window(self.hwnd, self.pid):
                    return result
                return None
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.01)

    def _read_frame(self, frame: _POINTER) -> CapturePixels | None:
        size = _Size()
        _check(
            method(frame, 8, HRESULT, ctypes.POINTER(_Size))(frame, ctypes.byref(size)),
            "ContentSize",
        )
        self._validate_size(size)
        if (size.width, size.height) != (self._size.width, self._size.height):
            # Return the old frame to the pool before recreating it on the next
            # request. Pixels outside ContentSize are undefined after a resize.
            self._size = size
            self._resize_pending = True
            return None
        surface = _POINTER()
        _check(
            method(frame, 6, HRESULT, _OUT)(frame, ctypes.byref(surface)),
            "Frame.Surface",
        )
        try:
            access = _query(surface, "a9b3d012-3df2-4ee3-b8d1-8695f457d3c1")
            try:
                texture = _POINTER()
                guid = GUID.parse("6f15aaf2-d208-4e89-9ab4-489535d34f9c")
                _check(
                    method(access, 3, HRESULT, ctypes.POINTER(GUID), _OUT)(
                        access, ctypes.byref(guid), ctypes.byref(texture)
                    ),
                    "GetInterface(Texture2D)",
                )
                try:
                    return self._read_texture(texture, size)
                finally:
                    release(texture)
            finally:
                release(access)
        finally:
            release(surface)

    def _read_texture(self, texture: _POINTER, size: _Size) -> CapturePixels | None:
        desc = _TextureDescription()
        method(texture, 10, None, ctypes.POINTER(_TextureDescription))(
            texture, ctypes.byref(desc)
        )
        if desc.format != 87:
            raise ValueError(f"Unexpected Windows capture format: {desc.format}")
        if size.width > desc.width or size.height > desc.height:
            # A frame can report the new ContentSize while still carrying an
            # older pool buffer. Recreate after returning it, never read beyond
            # its texture allocation or publish an incomplete resized image.
            self._resize_pending = True
            return None
        if (desc.width, desc.height) != self._texture_size:
            release(self._staging)
            self._staging = _POINTER()
            self._texture_size = (desc.width, desc.height)
            desc.usage, desc.bind, desc.cpu_access, desc.misc = 3, 0, 0x20000, 0
            _check(
                method(
                    self._device.device,
                    5,
                    HRESULT,
                    ctypes.POINTER(_TextureDescription),
                    _POINTER,
                    _OUT,
                )(
                    self._device.device,
                    ctypes.byref(desc),
                    None,
                    ctypes.byref(self._staging),
                ),
                "CreateTexture2D(staging)",
            )
        context = self._device.context
        method(context, 47, None, _POINTER, _POINTER)(context, self._staging, texture)
        mapped = _Mapped()
        _check(
            method(
                context,
                14,
                HRESULT,
                _POINTER,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.c_uint32,
                ctypes.POINTER(_Mapped),
            )(context, self._staging, 0, 1, 0, ctypes.byref(mapped)),
            "Map(staging)",
        )
        try:
            count = mapped.row_pitch * size.height
            if (
                not mapped.data
                or mapped.row_pitch < size.width * 4
                or count > 128 * 1024 * 1024
            ):
                raise ValueError("Invalid mapped capture texture")
            return rgba_pixels(
                ctypes.string_at(mapped.data, count),
                size.width,
                size.height,
                mapped.row_pitch,
            )
        finally:
            method(context, 15, None, _POINTER, ctypes.c_uint32)(
                context, self._staging, 0
            )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._resources.close()
            finally:
                release(self._staging)
                self._staging = _POINTER()
