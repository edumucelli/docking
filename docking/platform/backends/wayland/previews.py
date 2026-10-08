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
from typing import TYPE_CHECKING, Protocol

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from docking.platform.applications.matcher import AppIdMatcher
from docking.platform.applications.types import ApplicationMatch
from docking.platform.backends.base import (
    DisplayServer,
    PreviewImage,
    PreviewService,
    WindowId,
)

if TYPE_CHECKING:
    from docking.platform.applications.identity import ProcessIdentityService
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.backends.wayland.hyprland_ipc import HyprlandWindowService
    from docking.platform.backends.wayland.runtime import (
        HyprlandPreviewProtocolAdapter,
        PhocPreviewProtocolAdapter,
        PreviewProtocolAdapter,
    )
    from docking.platform.backends.wayland.toplevels import (
        WaylandForeignToplevelWindowService,
    )
    from docking.platform.model import DockModel

SHM_ARGB8888 = 0
SHM_XRGB8888 = 1
_PREFERRED_SHM_FORMATS = (SHM_ARGB8888, SHM_XRGB8888)

# Previews are scaled to the requested size, so the size is part of the identity.
_PreviewKey = tuple[WindowId, int, int]


class _CaptureResource(Protocol):
    def destroy(self) -> None: ...


class _ShmPool(_CaptureResource, Protocol):
    def create_buffer(
        self, offset: int, width: int, height: int, stride: int, format_: int
    ) -> _CaptureResource: ...


class _ShmProtocol(Protocol):
    def create_shm_pool(self, fd: int, size: int) -> _ShmPool: ...


@dataclass
class _PreviewToplevelState:
    handle: object
    title: str = ""
    app_id: str = ""
    identifier: str = ""
    application_match: ApplicationMatch | None = None
    closed: bool = False

    @property
    def desktop_id(self) -> str | None:
        return (
            self.application_match.desktop_id
            if self.application_match is not None
            else None
        )


@dataclass
class _CaptureRequest:
    window_id: WindowId
    requested_width: int
    requested_height: int
    source: _CaptureResource | None
    session: _CaptureResource | None
    width: int = 0
    height: int = 0
    shm_formats: set[int] = field(default_factory=set)
    frame: _CaptureResource | None = None
    fd: int | None = None
    mmap_obj: mmap.mmap | None = None
    buffer: _CaptureResource | None = None
    stride: int = 0
    format: int = SHM_ARGB8888
    y_inverted: bool = False


@dataclass
class _HyprlandCaptureRequest:
    window_id: WindowId
    requested_width: int
    requested_height: int
    frame: _CaptureResource | None
    width: int = 0
    height: int = 0
    fd: int | None = None
    mmap_obj: mmap.mmap | None = None
    buffer: _CaptureResource | None = None
    stride: int = 0
    format: int = SHM_ARGB8888
    y_inverted: bool = False


@dataclass
class _PhocCaptureRequest:
    window_id: WindowId
    requested_width: int
    requested_height: int
    frame: _CaptureResource | None
    width: int = 0
    height: int = 0
    fd: int | None = None
    mmap_obj: mmap.mmap | None = None
    buffer: _CaptureResource | None = None
    stride: int = 0
    format: int = SHM_ARGB8888
    y_inverted: bool = False


class WaylandPreviewHandleTracker:
    """Tracks ext-foreign-toplevel-list handles for preview capture."""

    def __init__(
        self,
        *,
        model: DockModel,
        application_registry: ApplicationRegistry,
        process_identity_service: ProcessIdentityService,
        protocol: object,
    ):
        self._model = model
        self._matcher = AppIdMatcher(
            registry=application_registry,
            process_identity_service=process_identity_service,
        )
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
        state.application_match = (
            self._matcher.match_result(state.app_id) if state.app_id else None
        )

    def _match_handle(
        self, *, desktop_id: str | None, app_id: str, title: str
    ) -> object | None:
        self._matcher.sync_visible_items(self._model.visible_items())
        matches = []
        for state in self._state_by_handle.values():
            if state.closed:
                continue
            if state.desktop_id is None:
                self._refresh_match(state)
            if desktop_id and state.desktop_id != desktop_id:
                continue
            if app_id and state.app_id != app_id and not desktop_id:
                continue
            if title and state.title != title:
                continue
            if desktop_id or app_id:
                matches.append(state.handle)
        # Native IPC and listing protocols do not share IDs. Ambiguous title/app
        # matches must not expose a different window's contents.
        return matches[0] if len(matches) == 1 else None


class WaylandPreviewService(PreviewService):
    """Nonblocking generic Wayland preview service."""

    def __init__(
        self, *, protocol: PreviewProtocolAdapter, handles: WaylandPreviewHandleTracker
    ):
        self._protocol = protocol
        self._handles = handles
        self._cache: dict[_PreviewKey, PreviewImage] = {}
        self._pending: dict[_PreviewKey, _CaptureRequest] = {}

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
        key = (window_id, width, height)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if key not in self._pending:
            self._start_capture(window_id=window_id, width=width, height=height)
        return None

    def _start_capture(self, *, window_id: WindowId, width: int, height: int) -> None:
        handle = self._handles.handle_for_window_id(window_id)
        if handle is None:
            return
        source = None
        try:
            source = self._protocol.create_source(handle)
            session = self._protocol.create_session(source)
        except Exception:
            if source is not None:
                with suppress(Exception):
                    source.destroy()
            return
        request = _CaptureRequest(
            window_id=window_id,
            requested_width=width,
            requested_height=height,
            source=source,
            session=session,
        )
        self._pending[_request_key(request)] = request
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
        if self._pending.get(_request_key(request)) is not request:
            return
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
        buffer = _allocate_shm_buffer(
            request,
            protocol=self._protocol,
            label="docking-wayland-preview",
            width=request.width,
            height=request.height,
            stride=stride,
            format_=format_,
        )
        frame = request.session.create_frame()
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
        if self._pending.get(_request_key(request)) is not request:
            return
        with suppress(Exception):
            self._cache[_request_key(request)] = _pixbuf_from_request(request)
        self._finish_request(request)

    def _on_stopped(self, request: _CaptureRequest) -> None:
        self._finish_failed(request)

    def _finish_failed(self, request: _CaptureRequest) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        _drop_window(self._cache, request.window_id)
        self._finish_request(request)

    def _finish_request(self, request: _CaptureRequest) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        self._pending.pop(_request_key(request), None)
        self._cleanup_request(request)

    def _cleanup_request(self, request: _CaptureRequest) -> None:
        _cleanup_capture_request(request)


class HyprlandPreviewService(PreviewService):
    """PreviewService backed by Hyprland's toplevel export protocol."""

    def __init__(
        self,
        *,
        protocol: HyprlandPreviewProtocolAdapter,
        windows: WaylandForeignToplevelWindowService | HyprlandWindowService,
    ):
        self._protocol = protocol
        self._windows = windows
        self._cache: dict[_PreviewKey, PreviewImage] = {}
        self._pending: dict[_PreviewKey, _HyprlandCaptureRequest] = {}

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
        key = (window_id, width, height)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if key not in self._pending:
            self._start_capture(window_id=window_id, width=width, height=height)
        return None

    def _start_capture(self, *, window_id: WindowId, width: int, height: int) -> None:
        handle = self._windows.protocol_handle_for_window_id(window_id)
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
        self._pending[_request_key(request)] = request
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
        if self._pending.get(_request_key(request)) is not request:
            return
        if request.width <= 0 or request.height <= 0 or request.stride <= 0:
            self._finish_failed(request)
            return
        try:
            buffer = _allocate_shm_buffer(
                request,
                protocol=self._protocol,
                label="docking-hyprland-preview",
                width=request.width,
                height=request.height,
                stride=request.stride,
                format_=request.format,
            )
            request.frame.copy(buffer, 1)
            self._protocol.flush()
        except Exception:
            self._finish_failed(request)

    def _on_flags(self, request: _HyprlandCaptureRequest, flags: int) -> None:
        request.y_inverted = bool(int(flags) & 1)

    def _on_ready(self, request: _HyprlandCaptureRequest) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        with suppress(Exception):
            self._cache[_request_key(request)] = _pixbuf_from_request(request)
        self._finish_request(request)

    def _finish_failed(self, request: _HyprlandCaptureRequest) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        _drop_window(self._cache, request.window_id)
        self._finish_request(request)

    def _finish_request(self, request: _HyprlandCaptureRequest) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        self._pending.pop(_request_key(request), None)
        self._cleanup_request(request)

    def _cleanup_request(self, request: _HyprlandCaptureRequest) -> None:
        _cleanup_capture_request(request)


class PhocPreviewService(PreviewService):
    """Window thumbnails provided by phoc's optional phosh_private protocol."""

    def __init__(
        self,
        *,
        protocol: PhocPreviewProtocolAdapter,
        windows: WaylandForeignToplevelWindowService,
    ):
        self._protocol = protocol
        self._windows = windows
        self._cache: dict[_PreviewKey, PreviewImage] = {}
        self._pending: dict[_PreviewKey, _PhocCaptureRequest] = {}

    def start(self) -> None:
        """The generic window service owns the foreign-toplevel handles."""

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
        key = (window_id, width, height)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if key not in self._pending:
            self._start_capture(window_id=window_id, width=width, height=height)
        return None

    def _start_capture(self, *, window_id: WindowId, width: int, height: int) -> None:
        handle = self._windows.protocol_handle_for_window_id(window_id)
        if handle is None:
            return
        try:
            frame = self._protocol.create_frame(handle, width, height)
        except Exception:
            return
        request = _PhocCaptureRequest(
            window_id=window_id,
            requested_width=width,
            requested_height=height,
            frame=frame,
        )
        self._pending[_request_key(request)] = request
        frame.dispatcher["buffer"] = lambda _frame, fmt, w, h, stride: self._on_buffer(
            request, fmt, w, h, stride
        )
        frame.dispatcher["flags"] = lambda _frame, flags: self._on_flags(request, flags)
        frame.dispatcher["ready"] = lambda _frame, *_timestamp: self._on_ready(request)
        frame.dispatcher["failed"] = lambda _frame: self._finish_failed(request)
        self._protocol.flush()

    def _on_buffer(
        self,
        request: _PhocCaptureRequest,
        format_: int,
        width: int,
        height: int,
        stride: int,
    ) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        format_ = int(format_)
        if format_ not in _PREFERRED_SHM_FORMATS:
            self._finish_failed(request)
            return
        request.format = format_
        request.width = int(width)
        request.height = int(height)
        request.stride = int(stride)
        if request.width <= 0 or request.height <= 0 or request.stride <= 0:
            self._finish_failed(request)
            return
        try:
            buffer = _allocate_shm_buffer(
                request,
                protocol=self._protocol,
                label="docking-phoc-preview",
                width=request.width,
                height=request.height,
                stride=request.stride,
                format_=request.format,
            )
            request.frame.copy(buffer)
            self._protocol.flush()
        except Exception:
            self._finish_failed(request)

    def _on_flags(self, request: _PhocCaptureRequest, flags: int) -> None:
        request.y_inverted = bool(int(flags) & 1)

    def _on_ready(self, request: _PhocCaptureRequest) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        with suppress(Exception):
            self._cache[_request_key(request)] = _pixbuf_from_request(request)
        self._finish_request(request)

    def _finish_failed(self, request: _PhocCaptureRequest) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        _drop_window(self._cache, request.window_id)
        self._finish_request(request)

    def _finish_request(self, request: _PhocCaptureRequest) -> None:
        if self._pending.get(_request_key(request)) is not request:
            return
        self._pending.pop(_request_key(request), None)
        self._cleanup_request(request)

    def _cleanup_request(self, request: _PhocCaptureRequest) -> None:
        _cleanup_capture_request(request)


def _request_key(
    request: _CaptureRequest | _HyprlandCaptureRequest | _PhocCaptureRequest,
) -> _PreviewKey:
    return (request.window_id, request.requested_width, request.requested_height)


def _drop_window(cache: dict[_PreviewKey, PreviewImage], window_id: WindowId) -> None:
    """Forget every cached size of one window."""
    for key in [key for key in cache if key[0] == window_id]:
        del cache[key]


def _allocate_shm_buffer(
    request: _CaptureRequest | _HyprlandCaptureRequest | _PhocCaptureRequest,
    *,
    protocol: _ShmProtocol,
    label: str,
    width: int,
    height: int,
    stride: int,
    format_: int,
) -> _CaptureResource:
    """Allocate capture storage while recording each resource immediately."""
    size = stride * height
    request.fd = os.memfd_create(label)
    os.ftruncate(request.fd, size)
    request.mmap_obj = mmap.mmap(request.fd, size)
    pool = protocol.create_shm_pool(request.fd, size)
    try:
        buffer = pool.create_buffer(0, width, height, stride, format_)
        request.buffer = buffer
        return buffer
    finally:
        with suppress(Exception):
            pool.destroy()


def _cleanup_capture_request(
    request: _CaptureRequest | _HyprlandCaptureRequest | _PhocCaptureRequest,
) -> None:
    """Release a capture request once, even if multiple terminal events arrive."""
    resources = (request.frame, request.buffer)
    request.frame = None
    request.buffer = None
    if isinstance(request, _CaptureRequest):
        resources += (request.session, request.source)
        request.session = None
        request.source = None
    for resource in resources:
        if resource is not None:
            with suppress(Exception):
                resource.destroy()
    mmap_obj = request.mmap_obj
    request.mmap_obj = None
    if mmap_obj is not None:
        with suppress(Exception):
            mmap_obj.close()
    fd = request.fd
    request.fd = None
    if fd is not None:
        with suppress(OSError):
            os.close(fd)


def _pixbuf_from_request(
    request: _CaptureRequest | _HyprlandCaptureRequest | _PhocCaptureRequest,
) -> PreviewImage:
    assert request.mmap_obj is not None
    source = request.mmap_obj[: request.stride * request.height]
    if request.y_inverted:
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
