# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Generic Wayland image-copy preview support.

This module deliberately fails closed. A compositor must expose
ext-foreign-toplevel-list, ext-foreign-toplevel image sources, image-copy
capture, and usable wl_shm constraints before Docking reports preview support.
"""

from __future__ import annotations

import mmap
import os
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from docking.platform.app_matcher import AppIdMatcher
from docking.platform.backends.base import (
    DisplayServer,
    PreviewImage,
    PreviewService,
    WindowId,
)

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel

SHM_ARGB8888 = 0
SHM_XRGB8888 = 1
_PREFERRED_SHM_FORMATS = (SHM_ARGB8888, SHM_XRGB8888)


@dataclass
class _PreviewToplevelState:
    handle: object
    title: str = ""
    app_id: str = ""
    identifier: str = ""
    desktop_id: str | None = None
    closed: bool = False


@dataclass
class _CaptureRequest:
    window_id: WindowId
    requested_width: int
    requested_height: int
    source: object
    session: object
    width: int = 0
    height: int = 0
    shm_formats: set[int] = field(default_factory=set)
    frame: object | None = None
    fd: int | None = None
    mmap_obj: mmap.mmap | None = None
    pool: object | None = None
    buffer: object | None = None
    stride: int = 0
    format: int = SHM_ARGB8888
    y_inverted: bool = False


@dataclass
class _HyprlandCaptureRequest:
    window_id: WindowId
    requested_width: int
    requested_height: int
    frame: object
    width: int = 0
    height: int = 0
    fd: int | None = None
    mmap_obj: mmap.mmap | None = None
    pool: object | None = None
    buffer: object | None = None
    stride: int = 0
    format: int = SHM_ARGB8888
    y_inverted: bool = False


class WaylandPreviewHandleTracker:
    """Tracks ext-foreign-toplevel-list handles for preview capture."""

    def __init__(self, *, model: DockModel, launcher: Launcher, protocol: object):
        self._model = model
        self._matcher = AppIdMatcher(launcher=launcher)
        self._protocol = protocol
        self._state_by_handle: dict[object, _PreviewToplevelState] = {}
        self._handle_by_window_id: dict[WindowId, object] = {}

    @property
    def capture_available(self) -> bool:
        available = getattr(self._protocol, "capture_available", False)
        return bool(available)

    def start(self) -> None:
        start = getattr(self._protocol, "start", None)
        if callable(start):
            start(self)

    def stop(self) -> None:
        stop = getattr(self._protocol, "stop", None)
        if callable(stop):
            stop()
        self._state_by_handle.clear()
        self._handle_by_window_id.clear()

    def toplevel_created(self, handle: object) -> None:
        self._state_by_handle.setdefault(
            handle,
            _PreviewToplevelState(handle=handle),
        )

    def title_changed(self, handle: object, title: str) -> None:
        self._ensure_state(handle).title = title or ""

    def app_id_changed(self, handle: object, app_id: str) -> None:
        state = self._ensure_state(handle)
        state.app_id = app_id.strip()
        self._refresh_match(state)

    def identifier_changed(self, handle: object, identifier: str) -> None:
        self._ensure_state(handle).identifier = identifier.strip()

    def done(self, handle: object) -> None:
        self._refresh_match(self._ensure_state(handle))

    def closed(self, handle: object) -> None:
        state = self._state_by_handle.pop(handle, None)
        if state is None:
            return
        state.closed = True
        for window_id, mapped in tuple(self._handle_by_window_id.items()):
            if mapped is handle:
                self._handle_by_window_id.pop(window_id, None)

    def associate_window(
        self,
        *,
        window_id: WindowId,
        desktop_id: str | None,
        app_id: str,
        title: str,
    ) -> None:
        handle = self._match_handle(
            desktop_id=desktop_id,
            app_id=app_id,
            title=title,
        )
        if handle is None:
            self._handle_by_window_id.pop(window_id, None)
            return
        self._handle_by_window_id[window_id] = handle

    def can_preview(self, window_id: WindowId) -> bool:
        return self.capture_available and window_id in self._handle_by_window_id

    def handle_for_window_id(self, window_id: WindowId) -> object | None:
        if not self.capture_available:
            return None
        return self._handle_by_window_id.get(window_id)

    def _ensure_state(self, handle: object) -> _PreviewToplevelState:
        self.toplevel_created(handle)
        return self._state_by_handle[handle]

    def _refresh_match(self, state: _PreviewToplevelState) -> None:
        self._matcher.sync_visible_items(self._model.visible_items())
        state.desktop_id = self._matcher.match(state.app_id) if state.app_id else None

    def _match_handle(
        self, *, desktop_id: str | None, app_id: str, title: str
    ) -> object | None:
        self._matcher.sync_visible_items(self._model.visible_items())
        for state in self._state_by_handle.values():
            if state.closed:
                continue
            if state.desktop_id is None:
                self._refresh_match(state)
            if desktop_id and state.desktop_id != desktop_id:
                continue
            if title and state.title and state.title == title:
                return state.handle
            if app_id and state.app_id and state.app_id == app_id:
                return state.handle
        return None


class WaylandPreviewService(PreviewService):
    """Nonblocking generic Wayland preview service."""

    def __init__(self, *, protocol: object, handles: WaylandPreviewHandleTracker):
        self._protocol = protocol
        self._handles = handles
        self._cache: dict[WindowId, PreviewImage] = {}
        self._pending: dict[WindowId, _CaptureRequest] = {}

    def start(self) -> None:
        """Start receiving ext-foreign-toplevel-list events."""
        self._handles.start()

    def stop(self) -> None:
        for request in tuple(self._pending.values()):
            self._cleanup_request(request)
        self._pending.clear()
        self._cache.clear()
        self._handles.stop()

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self._preview(window_id=window_id, width=width, height=height)

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self._preview(window_id=window_id, width=width, height=height)

    def _preview(
        self, *, window_id: WindowId, width: int, height: int
    ) -> PreviewImage | None:
        if window_id.backend is not DisplayServer.WAYLAND:
            return None
        cached = self._cache.get(window_id)
        if cached is not None:
            return cached
        if window_id not in self._pending:
            self._start_capture(window_id=window_id, width=width, height=height)
        return None

    def _start_capture(self, *, window_id: WindowId, width: int, height: int) -> None:
        handle = self._handles.handle_for_window_id(window_id)
        if handle is None:
            return
        try:
            source = self._protocol.create_source(handle)
            session = self._protocol.create_session(source)
        except Exception:
            return
        request = _CaptureRequest(
            window_id=window_id,
            requested_width=width,
            requested_height=height,
            source=source,
            session=session,
        )
        self._pending[window_id] = request
        session.dispatcher["buffer_size"] = lambda _session, w, h: self._on_buffer_size(
            request, w, h
        )
        session.dispatcher["shm_format"] = lambda _session, fmt: self._on_shm_format(
            request, fmt
        )
        session.dispatcher["done"] = lambda _session: self._on_constraints_done(request)
        session.dispatcher["stopped"] = lambda _session: self._on_stopped(request)
        self._protocol.flush()

    def _on_buffer_size(
        self, request: _CaptureRequest, width: int, height: int
    ) -> None:
        request.width = int(width)
        request.height = int(height)

    def _on_shm_format(self, request: _CaptureRequest, format_: int) -> None:
        request.shm_formats.add(int(format_))

    def _on_constraints_done(self, request: _CaptureRequest) -> None:
        if request.frame is not None:
            return
        if request.width <= 0 or request.height <= 0:
            self._finish_failed(request)
            return
        format_ = next(
            (fmt for fmt in _PREFERRED_SHM_FORMATS if fmt in request.shm_formats),
            None,
        )
        if format_ is None:
            self._finish_failed(request)
            return
        try:
            self._create_frame(request=request, format_=format_)
        except Exception:
            self._finish_failed(request)

    def _create_frame(self, *, request: _CaptureRequest, format_: int) -> None:
        stride = request.width * 4
        size = stride * request.height
        fd = os.memfd_create("docking-wayland-preview")
        os.ftruncate(fd, size)
        mmap_obj = mmap.mmap(fd, size)
        pool = self._protocol.create_shm_pool(fd, size)
        buffer = pool.create_buffer(0, request.width, request.height, stride, format_)
        pool.destroy()
        frame = request.session.create_frame()
        request.fd = fd
        request.mmap_obj = mmap_obj
        request.pool = pool
        request.buffer = buffer
        request.stride = stride
        request.format = format_
        request.frame = frame
        frame.dispatcher["ready"] = lambda _frame: self._on_frame_ready(request)
        frame.dispatcher["failed"] = lambda _frame, _reason: self._finish_failed(
            request
        )
        frame.attach_buffer(buffer)
        frame.damage_buffer(0, 0, request.width, request.height)
        frame.capture()
        self._protocol.flush()

    def _on_frame_ready(self, request: _CaptureRequest) -> None:
        with suppress(Exception):
            self._cache[request.window_id] = _pixbuf_from_request(request)
        self._finish_request(request)

    def _on_stopped(self, request: _CaptureRequest) -> None:
        self._finish_failed(request)

    def _finish_failed(self, request: _CaptureRequest) -> None:
        self._cache.pop(request.window_id, None)
        self._finish_request(request)

    def _finish_request(self, request: _CaptureRequest) -> None:
        self._pending.pop(request.window_id, None)
        self._cleanup_request(request)

    def _cleanup_request(self, request: _CaptureRequest) -> None:
        for attr in ("frame", "buffer", "session", "source"):
            obj = getattr(request, attr, None)
            destroy = getattr(obj, "destroy", None)
            if callable(destroy):
                with suppress(Exception):
                    destroy()
        if request.mmap_obj is not None:
            request.mmap_obj.close()
        if request.fd is not None:
            os.close(request.fd)


class HyprlandPreviewService(PreviewService):
    """PreviewService backed by Hyprland's toplevel export protocol."""

    def __init__(self, *, protocol: object, windows: object):
        self._protocol = protocol
        self._windows = windows
        self._cache: dict[WindowId, PreviewImage] = {}
        self._pending: dict[WindowId, _HyprlandCaptureRequest] = {}

    def start(self) -> None:
        """No separate toplevel-list tracker is needed for Hyprland export."""

    def stop(self) -> None:
        for request in tuple(self._pending.values()):
            self._cleanup_request(request)
        self._pending.clear()
        self._cache.clear()

    def capture(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self._preview(window_id=window_id, width=width, height=height)

    def thumbnail(
        self, window_id: WindowId, *, width: int, height: int
    ) -> PreviewImage | None:
        return self._preview(window_id=window_id, width=width, height=height)

    def _preview(
        self, *, window_id: WindowId, width: int, height: int
    ) -> PreviewImage | None:
        if window_id.backend is not DisplayServer.WAYLAND:
            return None
        cached = self._cache.get(window_id)
        if cached is not None:
            return cached
        if window_id not in self._pending:
            self._start_capture(window_id=window_id, width=width, height=height)
        return None

    def _start_capture(self, *, window_id: WindowId, width: int, height: int) -> None:
        handle_for_window_id = getattr(
            self._windows,
            "protocol_handle_for_window_id",
            None,
        )
        if not callable(handle_for_window_id):
            return
        handle = handle_for_window_id(window_id)
        if handle is None:
            return
        try:
            frame = self._protocol.create_frame(handle)
        except Exception:
            return
        request = _HyprlandCaptureRequest(
            window_id=window_id,
            requested_width=width,
            requested_height=height,
            frame=frame,
        )
        self._pending[window_id] = request
        frame.dispatcher["buffer"] = lambda _frame, fmt, w, h, stride: self._on_buffer(
            request, fmt, w, h, stride
        )
        frame.dispatcher["buffer_done"] = lambda _frame: self._on_buffer_done(request)
        frame.dispatcher["flags"] = lambda _frame, flags: self._on_flags(
            request,
            flags,
        )
        frame.dispatcher["ready"] = lambda _frame, *_timestamp: self._on_ready(request)
        frame.dispatcher["failed"] = lambda _frame: self._finish_failed(request)
        self._protocol.flush()

    def _on_buffer(
        self,
        request: _HyprlandCaptureRequest,
        format_: int,
        width: int,
        height: int,
        stride: int,
    ) -> None:
        format_ = int(format_)
        if format_ not in _PREFERRED_SHM_FORMATS:
            return
        if request.width and request.format == SHM_ARGB8888:
            return
        request.format = format_
        request.width = int(width)
        request.height = int(height)
        request.stride = int(stride)

    def _on_buffer_done(self, request: _HyprlandCaptureRequest) -> None:
        if request.width <= 0 or request.height <= 0 or request.stride <= 0:
            self._finish_failed(request)
            return
        try:
            size = request.stride * request.height
            fd = os.memfd_create("docking-hyprland-preview")
            os.ftruncate(fd, size)
            mmap_obj = mmap.mmap(fd, size)
            pool = self._protocol.create_shm_pool(fd, size)
            buffer = pool.create_buffer(
                0,
                request.width,
                request.height,
                request.stride,
                request.format,
            )
            pool.destroy()
            request.fd = fd
            request.mmap_obj = mmap_obj
            request.pool = pool
            request.buffer = buffer
            request.frame.copy(buffer, 1)
            self._protocol.flush()
        except Exception:
            self._finish_failed(request)

    def _on_flags(self, request: _HyprlandCaptureRequest, flags: int) -> None:
        request.y_inverted = bool(int(flags) & 1)

    def _on_ready(self, request: _HyprlandCaptureRequest) -> None:
        with suppress(Exception):
            self._cache[request.window_id] = _pixbuf_from_request(request)
        self._finish_request(request)

    def _finish_failed(self, request: _HyprlandCaptureRequest) -> None:
        self._cache.pop(request.window_id, None)
        self._finish_request(request)

    def _finish_request(self, request: _HyprlandCaptureRequest) -> None:
        self._pending.pop(request.window_id, None)
        self._cleanup_request(request)

    def _cleanup_request(self, request: _HyprlandCaptureRequest) -> None:
        for attr in ("frame", "buffer"):
            obj = getattr(request, attr, None)
            destroy = getattr(obj, "destroy", None)
            if callable(destroy):
                with suppress(Exception):
                    destroy()
        if request.mmap_obj is not None:
            request.mmap_obj.close()
        if request.fd is not None:
            os.close(request.fd)


def _pixbuf_from_request(
    request: _CaptureRequest | _HyprlandCaptureRequest,
) -> PreviewImage:
    assert request.mmap_obj is not None
    source = request.mmap_obj[: request.stride * request.height]
    if getattr(request, "y_inverted", False):
        rows = [
            source[index : index + request.stride]
            for index in range(0, len(source), request.stride)
        ]
        source = b"".join(reversed(rows))
    rgba = bytearray(len(source))
    for index in range(0, len(source), 4):
        b = source[index]
        g = source[index + 1]
        r = source[index + 2]
        a = source[index + 3] if request.format == SHM_ARGB8888 else 255
        rgba[index] = r
        rgba[index + 1] = g
        rgba[index + 2] = b
        rgba[index + 3] = a
    data = GLib.Bytes.new(bytes(rgba))
    pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
        data,
        GdkPixbuf.Colorspace.RGB,
        True,
        8,
        request.width,
        request.height,
        request.stride,
    )
    scaled = pixbuf.scale_simple(
        request.requested_width,
        request.requested_height,
        GdkPixbuf.InterpType.BILINEAR,
    )
    image = scaled if scaled is not None else pixbuf
    return PreviewImage(
        image=image,
        width=int(image.get_width()),
        height=int(image.get_height()),
    )
