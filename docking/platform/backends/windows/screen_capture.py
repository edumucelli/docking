"""Windows screen color sampling behind the common ScreenCaptureService."""

from __future__ import annotations

import ctypes
from typing import Protocol

from docking.log import get_logger
from docking.platform.backends.base import ScreenCaptureService
from docking.platform.desktop.implementations.windows.geometry import (
    WindowsMonitorGeometry,
)
from docking.platform.native.windows.ffi import bind

log = get_logger("windows.screen_capture")


class ScreenAPI(Protocol):
    def sample(self, x: int, y: int) -> tuple[int, int, int] | None: ...


class WindowsScreenAPI:
    def __init__(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        self._geometry = WindowsMonitorGeometry(user)
        self._get_dc = bind(user, "GetDC", ctypes.c_void_p, ctypes.c_void_p)
        self._release = bind(
            user, "ReleaseDC", ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p
        )
        self._pixel = bind(
            ctypes.WinDLL("gdi32", use_last_error=True),
            "GetPixel",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_int32,
        )
        self._flush = bind(
            ctypes.WinDLL("dwmapi", use_last_error=True), "DwmFlush", ctypes.c_int32
        )

    def sample(self, x: int, y: int) -> tuple[int, int, int] | None:
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is None:
            return None
        point = self._geometry.point(x, y, display)
        if point is None:
            return None
        # The shared applet has just destroyed its translucent input overlay.
        # Wait for that removal to reach composition before reading the desktop.
        if self._flush() < 0:
            return None
        dc = self._get_dc(None)
        if not dc:
            return None
        try:
            pixel = int(self._pixel(dc, *point))
            if pixel == 0xFFFFFFFF:  # CLR_INVALID, including unsupported devices.
                return None
            return pixel & 0xFF, (pixel >> 8) & 0xFF, (pixel >> 16) & 0xFF
        finally:
            if not self._release(None, dc):
                raise OSError("Windows did not release the screen device context")


class WindowsScreenCaptureService(ScreenCaptureService):
    def __init__(self, *, api: ScreenAPI | None = None) -> None:
        self._api = api
        self._stopped = False
        self._generation = 0

    def start(self) -> None:
        self._generation += 1
        self._stopped = False

    def stop(self) -> None:
        self._generation += 1
        self._stopped = True

    def pick_color(self, *, x: int, y: int) -> tuple[int, int, int] | None:
        if self._stopped:
            return None
        generation = self._generation
        try:
            if self._api is None:
                self._api = WindowsScreenAPI()
            result = self._api.sample(x, y)
        except OSError as exc:
            log.debug("Screen pixel query failed: %s", exc)
            return None
        if self._stopped or generation != self._generation:
            return None
        return result
