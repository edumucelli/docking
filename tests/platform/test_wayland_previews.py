"""Tests for generic Wayland preview support."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.items import DockItem
from docking.platform.backends.base import DisplayServer, PreviewImage, WindowId
from docking.platform.backends.wayland import previews as preview_mod
from docking.platform.backends.wayland.previews import (
    SHM_ARGB8888,
    HyprlandPreviewService,
    PhocPreviewService,
    WaylandPreviewHandleTracker,
    WaylandPreviewService,
)
from tests.platform.application_fakes import identity_services


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(
            return_value=[
                DockItem(
                    desktop_id="org.gnome.Nautilus.desktop",
                    wm_class="org.gnome.Nautilus",
                )
            ]
        )
    )


class FakeFrame:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}
        self.attach_buffer = MagicMock()
        self.damage_buffer = MagicMock()
        self.capture = MagicMock()
        self.copy = MagicMock()
        self.destroy = MagicMock()


class FakeSession:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}
        self.frame = FakeFrame()
        self.create_frame = MagicMock(return_value=self.frame)
        self.destroy = MagicMock()


class FakePool:
    def __init__(self) -> None:
        self.buffer = SimpleNamespace(destroy=MagicMock())
        self.create_buffer = MagicMock(return_value=self.buffer)
        self.destroy = MagicMock()


def test_wayland_preview_handle_tracker_matches_windows_to_capture_handles():
    protocol = SimpleNamespace(capture_available=True, start=MagicMock())
    tracker = WaylandPreviewHandleTracker(
        model=_model(),
        **identity_services(),
        protocol=protocol,
    )
    handle = object()
    window_id = WindowId(backend=DisplayServer.WAYLAND, value=7)

    tracker.toplevel_created(handle)
    tracker.title_changed(handle, "Files")
    tracker.app_id_changed(handle, "org.gnome.Nautilus")
    tracker.done(handle)
    tracker.associate_window(
        window_id=window_id,
        desktop_id="org.gnome.Nautilus.desktop",
        app_id="org.gnome.Nautilus",
        title="Files",
    )

    assert tracker.can_preview(window_id) is True
    assert tracker.handle_for_window_id(window_id) is handle


def test_preview_handle_is_not_guessed_from_an_ambiguous_application():
    tracker = WaylandPreviewHandleTracker(
        model=_model(),
        **identity_services(),
        protocol=SimpleNamespace(capture_available=True),
    )
    handles = [object(), object()]
    for handle in handles:
        tracker.toplevel_created(handle)
        tracker.title_changed(handle, "Files")
        tracker.app_id_changed(handle, "org.gnome.Nautilus")
    window_id = WindowId(DisplayServer.WAYLAND, "sway:123")
    tracker.associate_window(
        window_id=window_id,
        desktop_id="org.gnome.Nautilus.desktop",
        app_id="org.gnome.Nautilus",
        title="Files",
    )
    assert tracker.handle_for_window_id(window_id) is None
    tracker.closed(handles[0])
    tracker.associate_window(
        window_id=window_id,
        desktop_id="org.gnome.Nautilus.desktop",
        app_id="org.gnome.Nautilus",
        title="Files",
    )
    assert tracker.handle_for_window_id(window_id) is handles[1]


def test_wayland_preview_service_starts_once_and_returns_cached_frame(monkeypatch):
    window_id = WindowId(backend=DisplayServer.WAYLAND, value=7)
    handle = object()
    source = SimpleNamespace(destroy=MagicMock())
    session = FakeSession()
    pool = FakePool()
    protocol = SimpleNamespace(
        create_source=MagicMock(return_value=source),
        create_session=MagicMock(return_value=session),
        create_shm_pool=MagicMock(return_value=pool),
        flush=MagicMock(),
    )
    handles = SimpleNamespace(
        start=MagicMock(),
        stop=MagicMock(),
        handle_for_window_id=MagicMock(return_value=handle),
    )
    image = PreviewImage(image=object(), width=100, height=60)
    monkeypatch.setattr(
        preview_mod, "_pixbuf_from_request", MagicMock(return_value=image)
    )
    service = WaylandPreviewService(protocol=protocol, handles=handles)

    assert service.capture(window_id, width=100, height=60) is None
    assert service.capture(window_id, width=100, height=60) is None
    protocol.create_source.assert_called_once_with(handle)
    protocol.create_session.assert_called_once_with(source)

    session.dispatcher["buffer_size"](session, 320, 240)
    session.dispatcher["shm_format"](session, SHM_ARGB8888)
    session.dispatcher["done"](session)
    session.frame.dispatcher["ready"](session.frame)

    assert service.capture(window_id, width=100, height=60) is image

    session.frame.dispatcher["failed"](session.frame, 1)
    assert service.capture(window_id, width=100, height=60) is image
    assert session.frame.destroy.call_count == 1


def test_hyprland_preview_service_uses_wlr_handle_and_returns_cached_frame(
    monkeypatch,
):
    window_id = WindowId(backend=DisplayServer.WAYLAND, value=7)
    handle = object()
    frame = FakeFrame()
    pool = FakePool()
    protocol = SimpleNamespace(
        create_frame=MagicMock(return_value=frame),
        create_shm_pool=MagicMock(return_value=pool),
        flush=MagicMock(),
    )
    windows = SimpleNamespace(
        protocol_handle_for_window_id=MagicMock(return_value=handle),
    )
    image = PreviewImage(image=object(), width=100, height=60)
    monkeypatch.setattr(
        preview_mod,
        "_pixbuf_from_request",
        MagicMock(return_value=image),
    )
    service = HyprlandPreviewService(protocol=protocol, windows=windows)

    assert service.capture(window_id, width=100, height=60) is None
    assert service.capture(window_id, width=100, height=60) is None
    protocol.create_frame.assert_called_once_with(handle)

    frame.dispatcher["buffer"](frame, SHM_ARGB8888, 320, 240, 1280)
    frame.dispatcher["buffer_done"](frame)
    frame.copy.assert_called_once_with(pool.buffer, 1)
    frame.dispatcher["flags"](frame, 1)
    frame.dispatcher["ready"](frame, 0, 0, 0)

    assert service.capture(window_id, width=100, height=60) is image


def test_phoc_preview_service_copies_thumbnail_frame_immediately(monkeypatch):
    window_id = WindowId(backend=DisplayServer.WAYLAND, value=8)
    handle = object()
    frame = FakeFrame()
    pool = FakePool()
    protocol = SimpleNamespace(
        create_frame=MagicMock(return_value=frame),
        create_shm_pool=MagicMock(return_value=pool),
        flush=MagicMock(),
    )
    windows = SimpleNamespace(
        protocol_handle_for_window_id=MagicMock(return_value=handle),
    )
    image = PreviewImage(image=object(), width=96, height=64)
    monkeypatch.setattr(
        preview_mod,
        "_pixbuf_from_request",
        MagicMock(return_value=image),
    )
    service = PhocPreviewService(protocol=protocol, windows=windows)

    assert service.thumbnail(window_id, width=96, height=64) is None
    protocol.create_frame.assert_called_once_with(handle, 96, 64)

    frame.dispatcher["buffer"](frame, SHM_ARGB8888, 192, 128, 768)
    frame.copy.assert_called_once_with(pool.buffer)
    frame.dispatcher["ready"](frame, 0, 0, 0)

    assert service.thumbnail(window_id, width=96, height=64) is image


def test_capture_allocation_failure_releases_partial_resources(monkeypatch) -> None:
    mmap_obj = SimpleNamespace(close=MagicMock())
    close = MagicMock()
    monkeypatch.setattr(preview_mod.os, "memfd_create", MagicMock(return_value=51))
    monkeypatch.setattr(preview_mod.os, "ftruncate", MagicMock())
    monkeypatch.setattr(preview_mod.mmap, "mmap", MagicMock(return_value=mmap_obj))
    monkeypatch.setattr(preview_mod.os, "close", close)
    request = preview_mod._HyprlandCaptureRequest(
        window_id=WindowId(backend=DisplayServer.WAYLAND, value=9),
        requested_width=100,
        requested_height=60,
        frame=FakeFrame(),
    )
    protocol = SimpleNamespace(
        create_shm_pool=MagicMock(side_effect=RuntimeError("pool failed"))
    )

    with pytest.raises(RuntimeError, match="pool failed"):
        preview_mod._allocate_shm_buffer(
            request,
            protocol=protocol,
            label="test-preview",
            width=100,
            height=60,
            stride=400,
            format_=SHM_ARGB8888,
        )

    preview_mod._cleanup_capture_request(request)
    preview_mod._cleanup_capture_request(request)

    mmap_obj.close.assert_called_once_with()
    close.assert_called_once_with(51)
    assert request.fd is None
    assert request.mmap_obj is None


def test_wayland_session_creation_failure_releases_source() -> None:
    source = SimpleNamespace(destroy=MagicMock())
    protocol = SimpleNamespace(
        create_source=MagicMock(return_value=source),
        create_session=MagicMock(side_effect=RuntimeError("session failed")),
    )
    handles = SimpleNamespace(handle_for_window_id=lambda _: object())
    service = WaylandPreviewService(protocol=protocol, handles=handles)
    window_id = WindowId(DisplayServer.WAYLAND, 1)

    assert service.capture(window_id, width=100, height=60) is None

    source.destroy.assert_called_once()
    assert service._pending == {}


@pytest.mark.parametrize("kind", ["wayland", "hyprland", "phoc"])
def test_capture_ignores_buffer_events_after_stop(monkeypatch, kind) -> None:
    window_id = WindowId(DisplayServer.WAYLAND, 1)
    frame = FakeFrame()
    session = FakeSession()
    source = SimpleNamespace(destroy=MagicMock())
    handles = SimpleNamespace(
        handle_for_window_id=lambda _: object(),
        protocol_handle_for_window_id=lambda _: object(),
        stop=MagicMock(),
    )
    protocol = SimpleNamespace(
        create_frame=MagicMock(return_value=frame),
        create_source=MagicMock(return_value=source),
        create_session=MagicMock(return_value=session),
        flush=MagicMock(),
    )
    allocate = MagicMock()
    monkeypatch.setattr(preview_mod, "_allocate_shm_buffer", allocate)
    if kind == "wayland":
        service = WaylandPreviewService(protocol=protocol, handles=handles)
    elif kind == "hyprland":
        service = HyprlandPreviewService(protocol=protocol, windows=handles)
    else:
        service = PhocPreviewService(protocol=protocol, windows=handles)
    service.capture(window_id, width=100, height=60)
    if kind == "wayland":
        session.dispatcher["buffer_size"](session, 320, 240)
        session.dispatcher["shm_format"](session, SHM_ARGB8888)
    elif kind == "hyprland":
        frame.dispatcher["buffer"](frame, SHM_ARGB8888, 320, 240, 1280)

    service.stop()
    if kind == "wayland":
        session.dispatcher["done"](session)
    elif kind == "hyprland":
        frame.dispatcher["buffer_done"](frame)
    else:
        frame.dispatcher["buffer"](frame, SHM_ARGB8888, 320, 240, 1280)

    allocate.assert_not_called()
    assert service._pending == {}


class _SizedPreviewRig:
    """Drives the three native preview services through full capture cycles.

    Every capture gets its own fake frame/session, and ``_pixbuf_from_request``
    is replaced so the produced image reports the size that was requested.
    """

    def __init__(self, kind: str, monkeypatch) -> None:
        self.kind = kind
        self.captures: list[FakeSession | FakeFrame] = []
        handle = object()
        handles = SimpleNamespace(
            handle_for_window_id=lambda _: handle,
            protocol_handle_for_window_id=lambda _: handle,
            stop=MagicMock(),
            start=MagicMock(),
        )
        protocol = SimpleNamespace(
            create_frame=self._create_frame,
            create_source=lambda _handle: SimpleNamespace(destroy=MagicMock()),
            create_session=self._create_session,
            create_shm_pool=lambda _fd, _size: FakePool(),
            flush=MagicMock(),
        )
        monkeypatch.setattr(preview_mod, "_allocate_shm_buffer", MagicMock())
        monkeypatch.setattr(
            preview_mod,
            "_pixbuf_from_request",
            lambda request: PreviewImage(
                image=object(),
                width=request.requested_width,
                height=request.requested_height,
            ),
        )
        if kind == "wayland":
            self.service = WaylandPreviewService(protocol=protocol, handles=handles)
        elif kind == "hyprland":
            self.service = HyprlandPreviewService(protocol=protocol, windows=handles)
        else:
            self.service = PhocPreviewService(protocol=protocol, windows=handles)

    def _create_frame(self, *_args) -> FakeFrame:
        frame = FakeFrame()
        self.captures.append(frame)
        return frame

    def _create_session(self, _source) -> FakeSession:
        session = FakeSession()
        self.captures.append(session)
        return session

    def capture(self, window_id: WindowId, width: int, height: int):
        return self.service.capture(window_id, width=width, height=height)

    def complete(self, index: int) -> None:
        capture = self.captures[index]
        if self.kind == "wayland":
            capture.dispatcher["buffer_size"](capture, 320, 240)
            capture.dispatcher["shm_format"](capture, SHM_ARGB8888)
            capture.dispatcher["done"](capture)
            capture.frame.dispatcher["ready"](capture.frame)
        elif self.kind == "hyprland":
            capture.dispatcher["buffer"](capture, SHM_ARGB8888, 320, 240, 1280)
            capture.dispatcher["buffer_done"](capture)
            capture.dispatcher["ready"](capture, 0, 0, 0)
        else:
            capture.dispatcher["buffer"](capture, SHM_ARGB8888, 320, 240, 1280)
            capture.dispatcher["ready"](capture, 0, 0, 0)

    def fail(self, index: int) -> None:
        capture = self.captures[index]
        if self.kind == "wayland":
            capture.dispatcher["stopped"](capture)
        else:
            capture.dispatcher["failed"](capture)


_NATIVE_KINDS = ["wayland", "hyprland", "phoc"]
_WINDOW = WindowId(DisplayServer.WAYLAND, 1)


@pytest.mark.parametrize("kind", _NATIVE_KINDS)
@pytest.mark.parametrize("size", [(400, 300), (120, 90)])
def test_cached_preview_is_not_reused_for_a_different_size(
    monkeypatch, kind, size
) -> None:
    rig = _SizedPreviewRig(kind, monkeypatch)
    assert rig.capture(_WINDOW, 200, 150) is None
    rig.complete(0)
    first = rig.capture(_WINDOW, 200, 150)
    assert (first.width, first.height) == (200, 150)

    assert rig.capture(_WINDOW, *size) is None
    assert len(rig.captures) == 2
    rig.complete(1)

    resized = rig.capture(_WINDOW, *size)
    assert (resized.width, resized.height) == size
    assert rig.capture(_WINDOW, 200, 150) is first
    assert len(rig.captures) == 2


@pytest.mark.parametrize("kind", _NATIVE_KINDS)
def test_pending_captures_of_different_sizes_do_not_merge(monkeypatch, kind) -> None:
    rig = _SizedPreviewRig(kind, monkeypatch)
    assert rig.capture(_WINDOW, 200, 150) is None
    assert rig.capture(_WINDOW, 400, 300) is None
    assert rig.capture(_WINDOW, 400, 300) is None
    assert len(rig.captures) == 2

    rig.complete(1)
    large = rig.capture(_WINDOW, 400, 300)
    assert (large.width, large.height) == (400, 300)
    assert rig.capture(_WINDOW, 200, 150) is None
    assert len(rig.captures) == 2

    rig.complete(0)
    small = rig.capture(_WINDOW, 200, 150)
    assert (small.width, small.height) == (200, 150)
    assert rig.capture(_WINDOW, 400, 300) is large


@pytest.mark.parametrize("kind", _NATIVE_KINDS)
def test_failed_capture_drops_every_cached_size_of_that_window(
    monkeypatch, kind
) -> None:
    other = WindowId(DisplayServer.WAYLAND, 2)
    rig = _SizedPreviewRig(kind, monkeypatch)
    for index, (window, size) in enumerate(
        [(_WINDOW, (200, 150)), (_WINDOW, (400, 300)), (other, (200, 150))]
    ):
        rig.capture(window, *size)
        assert len(rig.captures) == index + 1
        rig.complete(index)
    assert len(rig.captures) == 3

    rig.capture(_WINDOW, 120, 90)
    rig.fail(3)

    assert rig.capture(other, 200, 150) is not None
    assert rig.capture(_WINDOW, 200, 150) is None
    assert rig.capture(_WINDOW, 400, 300) is None
    assert len(rig.captures) == 6
    assert len(rig.service._pending) == 2


@pytest.mark.parametrize("kind", _NATIVE_KINDS)
def test_stop_clears_every_cached_and_pending_size(monkeypatch, kind) -> None:
    rig = _SizedPreviewRig(kind, monkeypatch)
    rig.capture(_WINDOW, 200, 150)
    rig.complete(0)
    rig.capture(_WINDOW, 400, 300)

    rig.service.stop()

    assert rig.service._cache == {}
    assert rig.service._pending == {}
