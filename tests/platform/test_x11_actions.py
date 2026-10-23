"""Tests for X11 desktop action services."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult
from docking.platform.backends.x11.services import actions
from docking.platform.backends.x11.services.actions import WnckDesktopActionService


def test_show_desktop_toggles_current_wnck_state(monkeypatch):
    screen = MagicMock()
    screen.get_showing_desktop.return_value = False
    monkeypatch.setattr(
        actions.Wnck.Screen,
        "get_default",
        MagicMock(return_value=screen),
        raising=False,
    )
    service = WnckDesktopActionService()

    result = service.show_desktop()

    assert result is ActionResult.OK
    screen.force_update.assert_called_once_with()
    screen.toggle_showing_desktop.assert_called_once_with(True)


def test_show_desktop_uses_explicit_target(monkeypatch):
    screen = MagicMock()
    monkeypatch.setattr(
        actions.Wnck.Screen,
        "get_default",
        MagicMock(return_value=screen),
        raising=False,
    )
    service = WnckDesktopActionService()

    result = service.show_desktop(False)

    assert result is ActionResult.OK
    screen.toggle_showing_desktop.assert_called_once_with(False)
    screen.get_showing_desktop.assert_not_called()


def test_show_desktop_reports_not_found_without_screen(monkeypatch):
    monkeypatch.setattr(
        actions.Wnck.Screen,
        "get_default",
        MagicMock(return_value=None),
        raising=False,
    )
    service = WnckDesktopActionService()

    assert service.show_desktop() is ActionResult.NOT_FOUND
