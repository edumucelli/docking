"""Nonblocking Windows previews backed by worker-owned graphics capture."""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from typing import Protocol

from gi.repository import GdkPixbuf, GLib

from docking.log import get_logger
from docking.platform.backends.base import (
    DisplayServer,
    PreviewImage,
    PreviewService,
    WindowId,
    WindowSnapshot,
)
from docking.platform.native.windows.capture import (
    CapturePixels,
    WindowsCaptureDevice,
    WindowsCaptureStream,
    WindowsWindowTarget,
)

log = get_logger("windows.previews")
_Identity = tuple[WindowId, int, str | None, WindowsWindowTarget]
_REFRESH_SECONDS = 0.25
_STREAM_IDLE_SECONDS = 1.5
_CACHE_BYTES = 32 * 1024 * 1024
_MAX_PREVIEW_SIZE = 2048


class WindowsPreviewSource(Protocol):
    def list_all_windows(self) -> Sequence[WindowSnapshot]: ...
    def capture_target(
        self, window_id: WindowId, pid: int, app_id: str | None
    ) -> WindowsWindowTarget | None: ...
    def capture_is_current(
        self,
        window_id: WindowId,
        pid: int,
        app_id: str | None,
        target: WindowsWindowTarget,
    ) -> bool: ...


@dataclass(frozen=True)
class _Key:
    window_id: WindowId
    pid: int
    app_id: str | None
    target: WindowsWindowTarget
    width: int
    height: int

    @property
    def identity(self) -> _Identity:
        return self.window_id, self.pid, self.app_id, self.target


@dataclass(frozen=True)
class _Cached:
    image: PreviewImage
    captured_at: float


@dataclass(frozen=True)
class _Frame:
    pixbuf: GdkPixbuf.Pixbuf
    captured_at: float


def _pixbuf(pixels: CapturePixels) -> GdkPixbuf.Pixbuf:
    return GdkPixbuf.Pixbuf.new_from_bytes(
        GLib.Bytes.new(pixels.pixels),
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        pixels.width,
        pixels.height,
        pixels.width * 4,
    )


def _scaled(
    pixbuf: GdkPixbuf.Pixbuf, width: int, height: int
) -> GdkPixbuf.Pixbuf | None:
    ratio = min(width / pixbuf.get_width(), height / pixbuf.get_height(), 1.0)
    if ratio < 1.0:
        return pixbuf.scale_simple(
            max(1, round(pixbuf.get_width() * ratio)),
            max(1, round(pixbuf.get_height() * ratio)),
            GdkPixbuf.InterpType.BILINEAR,
        )
    return pixbuf


class WindowsPreviewService(PreviewService):
    asynchronous = True

    def __init__(
        self,
        *,
        windows: WindowsPreviewSource,
        availability_changed: Callable[[bool], None],
    ) -> None:
        self._windows = windows
        self._availability_changed = availability_changed
        self._cache: OrderedDict[_Key, _Cached] = OrderedDict()
        self._frames: OrderedDict[_Identity, _Frame] = OrderedDict()
        self._pending: set[_Identity] = set()
        self._retry_after: dict[_Identity, float] = {}
        self._queue: Queue[_Key] = Queue(maxsize=32)
        self._closed = Event()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._sources: set[int] = set()
        self._unavailable = False

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._closed.is_set():
                return
            self._thread = Thread(
                target=self._worker, name="windows-previews", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._closed.set()
        with self._lock:
            for source in self._sources:
                GLib.source_remove(source)
            self._sources.clear()
            self._cache.clear()
            self._frames.clear()
            self._pending.clear()
            self._retry_after.clear()
        self._availability_changed(False)
        if self._thread is not None:
            self._thread.join(timeout=0.5)

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        if window_id.backend is not DisplayServer.WINDOWS or not (
            0 < width <= _MAX_PREVIEW_SIZE and 0 < height <= _MAX_PREVIEW_SIZE
        ):
            return None
        row = next(
            (
                window
                for window in self._windows.list_all_windows()
                if window.id == window_id
            ),
            None,
        )
        if row is None or not row.pid or self._closed.is_set():
            with self._lock:
                self._drop_window(window_id)
            return None
        target = self._windows.capture_target(window_id, row.pid, row.app_id)
        if target is None:
            with self._lock:
                self._drop_window(window_id)
            return None
        key = _Key(window_id, row.pid, row.app_id, target, width, height)
        with self._lock:
            if self._unavailable:
                return None
            # A native host can reuse a frame for a different application without
            # changing its HWND or PID. Neither change may inherit cached pixels.
            for old in tuple(self._cache):
                if old.window_id == window_id and old.identity != key.identity:
                    self._cache.pop(old, None)
            for old_identity in tuple(self._frames):
                if old_identity[0] == window_id and old_identity != key.identity:
                    self._frames.pop(old_identity, None)
            frame = self._frames.get(key.identity)
            cached = self._cache.get(key)
            if cached is None and frame is not None:
                cached = self._fit_frame(key, frame)
            if cached is not None:
                self._frames.move_to_end(key.identity)
                if key in self._cache:
                    self._cache.move_to_end(key)
            if (
                (
                    cached is None
                    or time.monotonic() - cached.captured_at >= _REFRESH_SECONDS
                )
                and key.identity not in self._pending
                and time.monotonic() >= self._retry_after.get(key.identity, 0)
            ):
                self.start()
                try:
                    self._queue.put_nowait(key)
                    self._pending.add(key.identity)
                except Full:
                    pass
            return cached.image if cached is not None else None

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self.capture(window_id, width=width, height=height)

    def _drop_window(self, window_id: WindowId) -> None:
        for key in tuple(self._cache):
            if key.window_id == window_id:
                self._cache.pop(key, None)
        for identity in tuple(self._frames):
            if identity[0] == window_id:
                self._frames.pop(identity, None)

    def _fit_frame(self, key: _Key, frame: _Frame) -> _Cached | None:
        pixbuf = _scaled(frame.pixbuf, key.width, key.height)
        if pixbuf is None:
            self._trim()
            return None
        cached = _Cached(
            PreviewImage(pixbuf, pixbuf.get_width(), pixbuf.get_height()),
            frame.captured_at,
        )
        self._cache[key] = cached
        self._cache.move_to_end(key)
        self._trim()
        return cached

    def _trim(self) -> None:
        retained = sum(
            entry.image.width * entry.image.height * 4 for entry in self._cache.values()
        ) + sum(
            frame.pixbuf.get_width() * frame.pixbuf.get_height() * 4
            for frame in self._frames.values()
        )
        # Prefer retaining the source over fitted copies: a static or minimized
        # window may never supply another frame for a newly requested size.
        while self._cache and (retained > _CACHE_BYTES or len(self._cache) > 64):
            _old, entry = self._cache.popitem(last=False)
            retained -= entry.image.width * entry.image.height * 4
        while self._frames and (retained > _CACHE_BYTES or len(self._frames) > 64):
            identity, frame = self._frames.popitem(last=False)
            retained -= frame.pixbuf.get_width() * frame.pixbuf.get_height() * 4
            for key in tuple(self._cache):
                if key.identity == identity:
                    entry = self._cache.pop(key)
                    retained -= entry.image.width * entry.image.height * 4

    def _post(self, callback, *args) -> None:
        with self._lock:
            if self._closed.is_set():
                return
            source = 0

            def dispatch():
                with self._lock:
                    self._sources.discard(source)
                    if not self._closed.is_set():
                        callback(*args)
                return False

            source = GLib.idle_add(dispatch)
            self._sources.add(source)

    def _publish(self, key: _Key, pixels: CapturePixels | None) -> None:
        self._pending.discard(key.identity)
        row = next(
            (
                window
                for window in self._windows.list_all_windows()
                if window.id == key.window_id
            ),
            None,
        )
        if (
            row is None
            or (row.pid, row.app_id) != (key.pid, key.app_id)
            or not self._windows.capture_is_current(*key.identity)
        ):
            return
        if pixels is None:
            self._defer(key.identity)
            return
        pixbuf = _scaled(_pixbuf(pixels), _MAX_PREVIEW_SIZE, _MAX_PREVIEW_SIZE)
        if pixbuf is None:
            self._defer(key.identity)
            return
        frame = _Frame(pixbuf, time.monotonic())
        self._frames[key.identity] = frame
        self._frames.move_to_end(key.identity)
        for old in tuple(self._cache):
            if old.identity == key.identity:
                self._cache.pop(old, None)
        self._fit_frame(key, frame)

    def _defer(self, identity: _Identity) -> None:
        self._retry_after[identity] = time.monotonic() + 0.5
        while len(self._retry_after) > 64:
            self._retry_after.pop(next(iter(self._retry_after)))

    def _mark_unavailable(self) -> None:
        self._unavailable = True
        self._pending.clear()
        self._cache.clear()
        self._frames.clear()
        self._availability_changed(False)

    def _worker(self) -> None:
        device = None
        streams: OrderedDict[_Identity, tuple[WindowsCaptureStream, float]] = (
            OrderedDict()
        )
        try:
            device = WindowsCaptureDevice()
            self._post(self._availability_changed, True)
            while not self._closed.is_set():
                now = time.monotonic()
                for identity, (stream, touched) in tuple(streams.items()):
                    if now - touched > _STREAM_IDLE_SECONDS:
                        stream.close()
                        streams.pop(identity, None)
                try:
                    key = self._queue.get(timeout=0.1)
                except Empty:
                    continue
                identity = key.identity
                pixels = None
                try:
                    previous = streams.pop(identity, None)
                    if previous is None:
                        while len(streams) >= 4:
                            _old, (stream, _touched) = streams.popitem(last=False)
                            stream.close()
                        stream = WindowsCaptureStream(
                            device,
                            key.target.hwnd,
                            key.target.pid,
                            validate=lambda k=key: self._windows.capture_is_current(
                                *k.identity
                            ),
                        )
                    else:
                        stream = previous[0]
                    streams[identity] = (stream, time.monotonic())
                    pixels = stream.read()
                except (OSError, ValueError):
                    log.debug(
                        "Window capture unavailable for %s",
                        key.window_id,
                        exc_info=True,
                    )
                    failed = streams.pop(identity, None)
                    if failed is not None:
                        failed[0].close()
                self._post(self._publish, key, pixels)
        except (OSError, ValueError):
            log.debug("Windows graphics capture unavailable", exc_info=True)
            self._post(self._mark_unavailable)
        finally:
            for stream, _touched in streams.values():
                stream.close()
            if device is not None:
                device.close()
