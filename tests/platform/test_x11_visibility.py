"""Tests for X11 visibility service wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends.base import Rect
from docking.platform.backends.x11.impl.dodge import ScreenRect
from docking.platform.backends.x11.services import visibility


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
