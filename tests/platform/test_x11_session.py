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
