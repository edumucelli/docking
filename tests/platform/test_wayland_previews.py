"""Tests for generic Wayland preview support."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.base import DisplayServer, PreviewImage, WindowId
from docking.platform.backends.wayland import previews as preview_mod
from docking.platform.backends.wayland.previews import (
    SHM_ARGB8888,
    WaylandPreviewHandleTracker,
    WaylandPreviewService,
)


def _launcher() -> SimpleNamespace:
    resolved = {
        "org.gnome.Nautilus.desktop": SimpleNamespace(
            desktop_id="org.gnome.Nautilus.desktop"
        ),
    }
    return SimpleNamespace(
        resolve=MagicMock(side_effect=lambda desktop_id, **_: resolved.get(desktop_id)),
        resolve_by_wm_class=MagicMock(return_value=None),
    )


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(
            return_value=[
                SimpleNamespace(
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
        launcher=_launcher(),
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
