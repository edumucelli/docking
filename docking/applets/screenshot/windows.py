"""Native Windows desktop pixels and full-resolution foreground-window capture."""

from __future__ import annotations

import ctypes
import math
import sys
import threading
import time
from contextlib import closing

from PIL import ImageGrab

from docking.platform.backends.base import MonitorSnapshot, Rect, WindowSnapshot
from docking.platform.backends.windows.ffi import bind
from docking.platform.backends.windows.geometry import WindowsMonitorGeometry

from .backend import ScreenshotBackend, SelectionPhase
from .state import Mode, ScreenshotImage, ScreenshotResult, SelectedRegion
from .windows_selection import WindowsRegionCoordinates


class WindowsScreenshotAPI:
    def __init__(self) -> None:
        user = ctypes.WinDLL("user32", use_last_error=True)
        self._metric = bind(user, "GetSystemMetrics", ctypes.c_int32, ctypes.c_int32)
        self._flush = bind(ctypes.WinDLL("dwmapi"), "DwmFlush", ctypes.c_int32)

    def desktop(self, region: Rect | None = None) -> ScreenshotImage:
        bounds = Rect(*(self._metric(index) for index in (76, 77, 78, 79)))
        if bounds.width <= 0 or bounds.height <= 0:
            raise OSError("No capturable desktop")
        if region is not None and not (
            region.width > 0
            and region.height > 0
            and region.x >= bounds.x
            and region.y >= bounds.y
            and region.right <= bounds.right
            and region.bottom <= bounds.bottom
        ):
            raise OSError("Screenshot region is outside the desktop")
        target = region or bounds
        if target.width * target.height * 3 > 128 * 1024 * 1024:
            raise OSError("Screenshot exceeds the pixel budget")
        if self._flush() < 0:
            raise OSError("Desktop composition did not settle")
        bbox = (region.x, region.y, region.right, region.bottom) if region else None
        with closing(
            ImageGrab.grab(bbox=bbox, all_screens=True, include_layered_windows=True)
        ) as image:
            if image.size != (target.width, target.height):
                raise OSError("Display geometry changed during screenshot")
            with closing(image.convert("RGB")) as rgb:
                return ScreenshotImage(rgb.tobytes(), rgb.width, rgb.height)

    def window(
        self,
        cancelled: threading.Event,
        *,
        excluded: tuple[int, ...] = (),
        source: WindowSnapshot | None = None,
    ) -> ScreenshotImage | None:
        from docking.platform.backends.windows.capture import (
            WindowsCaptureDevice,
            WindowsCaptureStream,
        )
        from docking.platform.backends.windows.windows import Win32WindowAPI

        windows = Win32WindowAPI()
        deadline = time.monotonic() + 1.0
        selected = None
        if source is not None:
            if source.id.value in excluded:
                return None
            selected = windows.capture_snapshot(
                int(source.id.value), expected_pid=source.pid
            )
            if selected is None:
                return None
        while selected is None and not cancelled.is_set():
            selected = windows.foreground_capture_snapshot()
            if selected is not None and selected.id.value in excluded:
                selected = None
            if selected is not None or time.monotonic() >= deadline:
                break
            cancelled.wait(0.05)
        if selected is None or cancelled.is_set():
            return None
        target = windows.target(selected)
        if target is None:
            return None
        with (
            closing(WindowsCaptureDevice()) as device,
            closing(
                WindowsCaptureStream(
                    device,
                    target.hwnd,
                    target.pid,
                    validate=lambda: (
                        not cancelled.is_set() and windows.target(selected) == target
                    ),
                )
            ) as stream,
        ):
            deadline = time.monotonic() + 3.0
            while not cancelled.is_set() and time.monotonic() < deadline:
                pixels = stream.read(timeout=0.2)
                if pixels is not None:
                    return ScreenshotImage(
                        pixels.pixels, pixels.width, pixels.height, alpha=True
                    )
        return None


class WindowsScreenshotBackend(ScreenshotBackend):
    select_region = True

    def __init__(self, *, api: WindowsScreenshotAPI | None = None) -> None:
        self._api = api
        self._cancelled = threading.Event()
        self._excluded: tuple[int, ...] = ()
        self._source: WindowSnapshot | None = None
        self._coordinates: WindowsRegionCoordinates | None = None

    def prepare_capture(
        self, parent: object | None, *, source: WindowSnapshot | None = None
    ) -> None:
        from docking.platform.backends.windows.ffi import window_handle

        native = parent.get_window() if parent is not None else None
        handle = window_handle(native) if native is not None else None
        self._excluded = (handle,) if handle else ()
        self._source = source

    def begin_selection(self, window: object) -> None:
        self.end_selection()
        self._coordinates = WindowsRegionCoordinates(window)

    def selection_point(
        self, phase: SelectionPhase, x: float, y: float
    ) -> tuple[float, float] | None:
        return self._coordinates.point(phase) if self._coordinates is not None else None

    def end_selection(self) -> None:
        if self._coordinates is not None:
            self._coordinates.close()
            self._coordinates = None

    def available(self) -> bool:
        return sys.platform == "win32" and not self._cancelled.is_set()

    def prepare_region(self, region: SelectedRegion) -> Rect | None:
        from gi.repository import Gdk

        display = Gdk.Display.get_default()
        if display is None or region.width <= 0 or region.height <= 0:
            return None
        if not all(
            math.isfinite(v) for v in (region.x, region.y, region.right, region.bottom)
        ):
            return None
        geometry = WindowsMonitorGeometry()

        def mapping(x: float, y: float):
            monitor = display.get_monitor_at_point(math.floor(x), math.floor(y))
            if monitor is None:
                return None
            bounds = monitor.get_geometry()
            if not (
                bounds.x <= x < bounds.x + bounds.width
                and bounds.y <= y < bounds.y + bounds.height
            ):
                return None
            snapshot = MonitorSnapshot(
                index=next(
                    i
                    for i in range(display.get_n_monitors())
                    if display.get_monitor(i) == monitor
                ),
                geometry=Rect(bounds.x, bounds.y, bounds.width, bounds.height),
                scale=monitor.get_scale_factor(),
            )
            return geometry.mapping(snapshot, display)

        start = mapping(region.x, region.y)
        end = mapping(
            math.nextafter(region.right, -math.inf),
            math.nextafter(region.bottom, -math.inf),
        )
        if start is None or end is None:
            return None
        x = start.physical.x + round((region.x - start.logical.x) * start.scale)
        y = start.physical.y + round((region.y - start.logical.y) * start.scale)
        right = end.physical.x + round((region.right - end.logical.x) * end.scale)
        bottom = end.physical.y + round((region.bottom - end.logical.y) * end.scale)
        return Rect(x, y, right - x, bottom - y) if right > x and bottom > y else None

    def capture(self, mode: Mode, *, region: Rect | None = None) -> ScreenshotResult:
        if self._cancelled.is_set():
            return ScreenshotResult(False)
        if mode == "region" and region is None:
            return ScreenshotResult(False)
        if self._api is None:
            self._api = WindowsScreenshotAPI()
        image = (
            self._api.window(
                self._cancelled, excluded=self._excluded, source=self._source
            )
            if mode == "window"
            else self._api.desktop(region if mode == "region" else None)
        )
        return (
            ScreenshotResult(image is not None, image)
            if not self._cancelled.is_set()
            else ScreenshotResult(False)
        )

    def stop(self) -> None:
        self._cancelled.set()
        self.end_selection()
