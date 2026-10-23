"""Tests for X11 window-picking service."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult, DisplayServer, WindowId
from docking.platform.backends.x11.services import picking
from docking.platform.backends.x11.services.picking import WnckWindowPickService


def _window(
    xid: int,
    *,
    pid: int = 123,
    name: str = "Window",
    normal: bool = True,
    minimized: bool = False,
    geometry: tuple[int, int, int, int] = (0, 0, 100, 100),
):
    window = MagicMock()
    window.get_xid.return_value = xid
    window.get_pid.return_value = pid
    window.get_name.return_value = name
    window.get_window_type.return_value = (
        picking.Wnck.WindowType.NORMAL if normal else object()
    )
    window.is_minimized.return_value = minimized
    window.get_geometry.return_value = geometry
    return window


def test_pick_window_at_returns_topmost_normal_window(monkeypatch):
    skipped_type = _window(1, normal=False)
    skipped_minimized = _window(2, minimized=True)
    target = _window(3, name="Firefox", geometry=(10, 10, 60, 60))
    screen = MagicMock()
    screen.get_windows_stacked.return_value = [target, skipped_minimized, skipped_type]
    monkeypatch.setattr(picking.Wnck.Screen, "get_default", lambda: screen)

    snapshot = WnckWindowPickService().pick_window_at(x=20, y=20)

    assert snapshot is not None
    assert snapshot.id == WindowId.x11(3)
    assert snapshot.title == "Firefox"
    screen.force_update.assert_called_once_with()


def test_pid_and_kill_use_xid_lookup(monkeypatch):
    target = _window(7, pid=123)
    screen = MagicMock()
    screen.get_windows.return_value = [target]
    monkeypatch.setattr(picking.Wnck.Screen, "get_default", lambda: screen)
    killed = MagicMock(return_value=True)
    monkeypatch.setattr(picking, "kill_pid", killed)
    service = WnckWindowPickService()

    assert service.pid_for(WindowId.x11(7)) == 123
    assert service.kill(WindowId.x11(7)) is ActionResult.OK
    killed.assert_called_once_with(pid=123)


def test_missing_or_invalid_window_returns_safe_results(monkeypatch):
    screen = MagicMock()
    screen.get_windows.return_value = []
    monkeypatch.setattr(picking.Wnck.Screen, "get_default", lambda: screen)
    service = WnckWindowPickService()

    assert service.pid_for(WindowId(DisplayServer.WAYLAND, "1")) is None
    assert service.kill(WindowId.x11(99)) is ActionResult.NOT_FOUND
