"""Tests for X11 runtime construction helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends.x11 import session
from docking.platform.backends.x11.services.windows import X11WindowService
from tests.platform.application_fakes import identity_services


def test_x11_session_backend_groups_x11_services(monkeypatch):
    windows = MagicMock()
    previews = MagicMock()
    visibility = MagicMock()
    desktop_actions = MagicMock()
    workspaces = MagicMock()
    window_picker = MagicMock()
    idle = MagicMock()
    screen_capture = MagicMock()
    config = MagicMock()
    monkeypatch.setattr(session, "X11WindowService", MagicMock(return_value=windows))
    monkeypatch.setattr(session, "X11PreviewService", MagicMock(return_value=previews))
    monkeypatch.setattr(
        session, "X11VisibilityService", MagicMock(return_value=visibility)
    )
    monkeypatch.setattr(
        session, "WnckDesktopActionService", MagicMock(return_value=desktop_actions)
    )
    monkeypatch.setattr(
        session, "WnckWorkspaceService", MagicMock(return_value=workspaces)
    )
    monkeypatch.setattr(
        session, "WnckWindowPickService", MagicMock(return_value=window_picker)
    )
    monkeypatch.setattr(session, "X11IdleService", MagicMock(return_value=idle))
    monkeypatch.setattr(
        session, "X11ScreenCaptureService", MagicMock(return_value=screen_capture)
    )

    backend = session.X11SessionBackend(
        model=MagicMock(), config=config, **identity_services()
    )

    assert backend.name == "x11"
    assert backend.display_server is session.DisplayServer.X11
    assert backend.windows is windows
    assert backend.previews is previews
    assert backend.visibility is visibility
    assert backend._services.windows is windows
    assert backend._services.previews is previews
    assert backend._services.visibility is visibility
    assert backend._services.workspaces is workspaces
    assert backend._services.window_picker is window_picker
    assert backend._services.idle is idle
    assert backend._services.screen_capture is screen_capture
    assert backend._services.desktop_actions is desktop_actions
    assert backend.workspaces is workspaces
    assert backend.desktop_actions is desktop_actions
    assert backend.screen_capture is screen_capture
    assert backend.idle is idle
    assert backend.window_picker is window_picker
    assert backend.capabilities.tracks_windows is True
    assert backend.capabilities.supports_window_menu is True
    assert backend.capabilities.supports_screen_reservation is True
    assert backend.capabilities.supports_overlap_active is True
    session.X11PreviewService.assert_called_once_with(window_tracker=windows)
    session.X11VisibilityService.assert_called_once_with(config=config)
    session.WnckDesktopActionService.assert_called_once_with()
    session.WnckWorkspaceService.assert_called_once_with()
    session.WnckWindowPickService.assert_called_once_with()
    session.X11IdleService.assert_called_once_with()
    session.X11ScreenCaptureService.assert_called_once_with()


def test_x11_session_backend_lifecycle_starts_and_stops_services(monkeypatch):
    windows = MagicMock()
    previews = MagicMock()
    visibility = MagicMock()
    workspaces = MagicMock()
    window_picker = MagicMock()
    idle = MagicMock()
    screen_capture = MagicMock()
    desktop_actions = MagicMock()
    monkeypatch.setattr(session, "X11WindowService", MagicMock(return_value=windows))
    monkeypatch.setattr(session, "X11PreviewService", MagicMock(return_value=previews))
    monkeypatch.setattr(
        session, "X11VisibilityService", MagicMock(return_value=visibility)
    )
    monkeypatch.setattr(
        session, "WnckWorkspaceService", MagicMock(return_value=workspaces)
    )
    monkeypatch.setattr(
        session, "WnckWindowPickService", MagicMock(return_value=window_picker)
    )
    monkeypatch.setattr(session, "X11IdleService", MagicMock(return_value=idle))
    monkeypatch.setattr(
        session, "X11ScreenCaptureService", MagicMock(return_value=screen_capture)
    )
    monkeypatch.setattr(
        session, "WnckDesktopActionService", MagicMock(return_value=desktop_actions)
    )
    backend = session.X11SessionBackend(
        model=MagicMock(), config=MagicMock(), **identity_services()
    )

    backend.start()
    backend.stop()

    windows.start.assert_called_once_with()
    previews.start.assert_called_once_with()
    visibility.start.assert_called_once_with()
    workspaces.start.assert_called_once_with()
    window_picker.start.assert_called_once_with()
    idle.start.assert_called_once_with()
    screen_capture.start.assert_called_once_with()
    desktop_actions.start.assert_called_once_with()
    desktop_actions.stop.assert_called_once_with()
    screen_capture.stop.assert_called_once_with()
    idle.stop.assert_called_once_with()
    window_picker.stop.assert_called_once_with()
    workspaces.stop.assert_called_once_with()
    visibility.stop.assert_called_once_with()
    previews.stop.assert_called_once_with()
    windows.stop.assert_called_once_with()


def test_x11_session_backend_always_exposes_window_service():
    model = MagicMock()
    model.visible_items.return_value = []
    backend = session.X11SessionBackend(
        model=model,
        config=MagicMock(),
        **identity_services(),
    )

    assert isinstance(backend.windows, X11WindowService)
