"""Retrieve Windows shell icons on two bounded background workers.

Gio does not supply icons for many packaged apps. The shell image factory
knows their registered artwork. COM and GDI handles stay on the worker thread;
only immutable straight-alpha RGBA pixels cross back to GTK's main loop.
"""

from __future__ import annotations

import ctypes
from collections import OrderedDict
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, Thread

from gi.repository import GLib

from docking.log import get_logger
from docking.platform.applications.types import ApplicationInfo

from .com import GUID, HRESULT, apartment, method, release
from .native import _bind

log = get_logger("windows.icons")


@dataclass(frozen=True)
class NativeIcon:
    pixels: bytes
    width: int
    height: int


class _Size(ctypes.Structure):
    _fields_ = [("width", ctypes.c_int32), ("height", ctypes.c_int32)]


class _Bitmap(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int32),
        ("width", ctypes.c_int32),
        ("height", ctypes.c_int32),
        ("stride", ctypes.c_int32),
        ("planes", ctypes.c_uint16),
        ("bits", ctypes.c_uint16),
        ("pixels", ctypes.c_void_p),
    ]


class _BitmapHeader(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("width", ctypes.c_int32),
        ("height", ctypes.c_int32),
        ("planes", ctypes.c_uint16),
        ("bits", ctypes.c_uint16),
        ("compression", ctypes.c_uint32),
        ("image_size", ctypes.c_uint32),
        ("x_resolution", ctypes.c_int32),
        ("y_resolution", ctypes.c_int32),
        ("colors", ctypes.c_uint32),
        ("important_colors", ctypes.c_uint32),
    ]


def _bitmap_pixels(bitmap: wintypes.HBITMAP) -> NativeIcon | None:
    gdi = ctypes.WinDLL("gdi32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    get_object = _bind(
        gdi, "GetObjectW", ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p
    )
    description = _Bitmap()
    if not get_object(bitmap, ctypes.sizeof(description), ctypes.byref(description)):
        return None
    width, height = description.width, abs(description.height)
    if not (0 < width <= 1024 and 0 < height <= 1024):
        return None
    header = _BitmapHeader(
        size=ctypes.sizeof(_BitmapHeader),
        width=width,
        height=-height,
        planes=1,
        bits=32,
    )
    pixels = ctypes.create_string_buffer(width * height * 4)
    get_dc = _bind(user, "GetDC", wintypes.HDC, wintypes.HWND)
    release_dc = _bind(user, "ReleaseDC", ctypes.c_int, wintypes.HWND, wintypes.HDC)
    get_bits = _bind(
        gdi,
        "GetDIBits",
        ctypes.c_int,
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    )
    dc = get_dc(None)
    if not dc:
        return None
    try:
        if get_bits(dc, bitmap, 0, height, pixels, ctypes.byref(header), 0) != height:
            return None
    finally:
        release_dc(None, dc)
    # Shell bitmaps use premultiplied BGRA; GdkPixbuf expects straight RGBA.
    bgra = pixels.raw
    opaque = not any(bgra[3::4])
    rgba = bytearray(len(bgra))
    for offset in range(0, len(bgra), 4):
        blue, green, red, alpha = bgra[offset : offset + 4]
        if opaque:
            alpha = 255
        if 0 < alpha < 255:
            red, green, blue = (
                min(255, channel * 255 // alpha) for channel in (red, green, blue)
            )
        rgba[offset : offset + 4] = bytes((red, green, blue, alpha))
    return NativeIcon(bytes(rgba), width, height)


def extract_icon(target: str, size: int) -> NativeIcon | None:
    """Blocking extraction; callers must use a background thread."""
    with apartment():
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        create_item = _bind(
            shell,
            "SHCreateItemFromParsingName",
            HRESULT,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        )
        iid = GUID.parse("bcc18b79-ba16-442f-80c4-8a59c30c463b")
        factory = ctypes.c_void_p()
        if create_item(target, None, ctypes.byref(iid), ctypes.byref(factory)) < 0:
            return None
        try:
            bitmap = wintypes.HBITMAP()
            get_image = method(
                factory,
                3,
                HRESULT,
                _Size,
                wintypes.UINT,
                ctypes.POINTER(wintypes.HBITMAP),
            )
            # ICONONLY | SCALEUP, with exact requested bounds.
            if get_image(factory, _Size(size, size), 0x104, ctypes.byref(bitmap)) < 0:
                return None
            try:
                return _bitmap_pixels(bitmap)
            finally:
                gdi = ctypes.WinDLL("gdi32", use_last_error=True)
                _bind(gdi, "DeleteObject", wintypes.BOOL, ctypes.c_void_p)(bitmap)
        finally:
            release(factory)


class WindowsIconCache:
    """Main-loop cache with bounded queued work and daemon worker lifetime."""

    def __init__(self, on_ready: Callable[[str], None]) -> None:
        self._on_ready = on_ready
        self._images: OrderedDict[tuple[str, int], NativeIcon | None] = OrderedDict()
        self._pending: set[tuple[str, int]] = set()
        self._queue: Queue[tuple[tuple[str, int], str]] = Queue(maxsize=256)
        self._closed = Event()
        for index in range(2):
            Thread(
                target=self._worker, name=f"windows-icons-{index}", daemon=True
            ).start()

    def get(self, info: ApplicationInfo, size: int) -> NativeIcon | None:
        key = (info.desktop_id, max(1, min(size, 512)))
        if key in self._images:
            self._images.move_to_end(key)
            return self._images[key]
        if key in self._pending or self._closed.is_set():
            return None
        target = (
            "shell:AppsFolder\\" + info.desktop_id
            if "!" in info.desktop_id
            else str(info.executable_path)
            if info.executable_path
            else ""
        )
        if not target:
            return None
        self._pending.add(key)
        try:
            self._queue.put_nowait((key, target))
        except Full:
            self._pending.discard(key)
        return None

    def stop(self) -> None:
        self._closed.set()

    def _worker(self) -> None:
        while not self._closed.is_set():
            try:
                key, target = self._queue.get(timeout=0.5)
            except Empty:
                continue
            image = None
            try:
                image = extract_icon(target, key[1])
            except Exception:
                log.debug("Windows shell icon extraction failed", exc_info=True)
            if not self._closed.is_set():
                GLib.idle_add(self._publish, key, image)

    def _publish(self, key: tuple[str, int], image: NativeIcon | None) -> bool:
        if self._closed.is_set():
            return False
        self._pending.discard(key)
        self._images[key] = image
        while len(self._images) > 256:
            self._images.popitem(last=False)
        if image is not None:
            self._on_ready(key[0])
        return False
