"""Tests for X11 runtime construction helpers."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends.x11 import session


def test_build_x11_window_tracker_defaults_to_service(monkeypatch):
    service = MagicMock()
    service_cls = MagicMock(return_value=service)
    legacy_cls = MagicMock()
    monkeypatch.delenv(session.X11_WINDOW_SERVICE_ENV, raising=False)
    monkeypatch.setattr(session, "X11WindowService", service_cls)
    monkeypatch.setattr(session, "WindowTracker", legacy_cls)

    result = session.build_x11_window_tracker(
        model=MagicMock(), launcher=MagicMock(), config=MagicMock()
    )

    assert result is service
    service_cls.assert_called_once()
    legacy_cls.assert_not_called()


def test_build_x11_window_tracker_allows_legacy_fallback(monkeypatch):
    legacy = MagicMock()
    service_cls = MagicMock()
    legacy_cls = MagicMock(return_value=legacy)
    monkeypatch.setenv(session.X11_WINDOW_SERVICE_ENV, "legacy")
    monkeypatch.setattr(session, "X11WindowService", service_cls)
    monkeypatch.setattr(session, "WindowTracker", legacy_cls)

    result = session.build_x11_window_tracker(
        model=MagicMock(), launcher=MagicMock(), config=MagicMock()
    )

    assert result is legacy
    legacy_cls.assert_called_once()
    service_cls.assert_not_called()


def test_build_x11_window_tracker_ignores_invalid_mode(monkeypatch):
    service = MagicMock()
    service_cls = MagicMock(return_value=service)
    legacy_cls = MagicMock()
    monkeypatch.setenv(session.X11_WINDOW_SERVICE_ENV, "invalid")
    monkeypatch.setattr(session, "X11WindowService", service_cls)
    monkeypatch.setattr(session, "WindowTracker", legacy_cls)

    result = session.build_x11_window_tracker(
        model=MagicMock(), launcher=MagicMock(), config=MagicMock()
    )

    assert result is service
    service_cls.assert_called_once()
    legacy_cls.assert_not_called()


def test_build_x11_session_backend_groups_x11_services(monkeypatch):
    windows = MagicMock()
    previews = MagicMock()
    monkeypatch.setattr(
        session, "build_x11_window_tracker", MagicMock(return_value=windows)
    )
    monkeypatch.setattr(session, "X11PreviewService", MagicMock(return_value=previews))

    backend = session.build_x11_session_backend(
        model=MagicMock(), launcher=MagicMock(), config=MagicMock()
    )

    assert backend.name == "x11"
    assert backend.display_server is session.DisplayServer.X11
    assert backend.windows is windows
    assert backend.previews is previews
    assert backend.workspaces is None
    assert backend.desktop_actions is None
    assert backend.screen_capture is None
    assert backend.idle is None
    assert backend.window_picker is None
    assert backend.capabilities.tracks_windows is True
    assert backend.capabilities.supports_window_menu is True
    assert backend.capabilities.supports_screen_reservation is True
    session.X11PreviewService.assert_called_once_with(window_tracker=windows)


def test_x11_session_backend_lifecycle_starts_and_stops_services(monkeypatch):
    windows = MagicMock()
    previews = MagicMock()
    monkeypatch.setattr(
        session, "build_x11_window_tracker", MagicMock(return_value=windows)
    )
    monkeypatch.setattr(session, "X11PreviewService", MagicMock(return_value=previews))
    backend = session.X11SessionBackend(
        model=MagicMock(), launcher=MagicMock(), config=MagicMock()
    )

    backend.start()
    backend.stop()

    windows.start.assert_called_once_with()
    previews.start.assert_called_once_with()
    previews.stop.assert_called_once_with()
    windows.stop.assert_called_once_with()


def test_x11_session_backend_allows_legacy_tracker_without_lifecycle(monkeypatch):
    windows = object()
    previews = MagicMock()
    monkeypatch.setattr(
        session, "build_x11_window_tracker", MagicMock(return_value=windows)
    )
    monkeypatch.setattr(session, "X11PreviewService", MagicMock(return_value=previews))
    backend = session.X11SessionBackend(
        model=MagicMock(), launcher=MagicMock(), config=MagicMock()
    )

    backend.start()
    backend.stop()

    previews.start.assert_called_once_with()
    previews.stop.assert_called_once_with()
