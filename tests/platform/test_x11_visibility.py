"""Tests for X11 visibility service wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.core.config import HideMode
from docking.platform.backends.base import Rect
from docking.platform.backends.x11 import visibility
from docking.platform.dodge import ScreenRect


def test_create_monitor_preserves_window_dodge_monitor_arguments(monkeypatch):
    config = MagicMock()
    monitor = MagicMock()
    monitor_cls = MagicMock(return_value=monitor)
    monkeypatch.setattr(visibility, "WindowDodgeMonitor", monitor_cls)
    get_dock_rect = MagicMock(return_value=Rect(x=10, y=20, width=300, height=40))
    on_change = MagicMock()
    service = visibility.X11VisibilityService(config=config)

    result = service.create_monitor(
        get_dock_rect=get_dock_rect,
        on_change=on_change,
    )

    assert result is monitor
    monitor_cls.assert_called_once()
    kwargs = monitor_cls.call_args.kwargs
    assert kwargs["config"] is config
    assert kwargs["on_change"] is on_change
    assert kwargs["get_dock_rect"]() == ScreenRect(
        x=10,
        y=20,
        width=300,
        height=40,
    )


def test_create_monitor_preserves_unrealized_none_rect(monkeypatch):
    config = MagicMock()
    monitor_cls = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(visibility, "WindowDodgeMonitor", monitor_cls)
    service = visibility.X11VisibilityService(config=config)

    service.create_monitor(get_dock_rect=lambda: None, on_change=MagicMock())

    assert monitor_cls.call_args.kwargs["get_dock_rect"]() is None


def test_create_monitor_returns_none_without_config():
    service = visibility.X11VisibilityService(config=None)

    result = service.create_monitor(get_dock_rect=MagicMock(), on_change=MagicMock())

    assert result is None


def test_supports_only_overlap_hide_modes():
    service = visibility.X11VisibilityService(config=MagicMock())

    assert service.supports_hide_mode(HideMode.INTELLIGENT) is True
    assert service.supports_hide_mode(HideMode.DODGE_ACTIVE) is True
    assert service.supports_hide_mode(HideMode.WINDOW_DODGE) is True
    assert service.supports_hide_mode(HideMode.DODGE_MAXIMIZED) is True
    assert service.supports_hide_mode(HideMode.NONE) is False
    assert service.supports_hide_mode(HideMode.AUTOHIDE) is False
    assert service.supports_hide_mode(object()) is False
