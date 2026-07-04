"""Tests for preferences action delegation."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.ui.settings import SettingsActions


def test_settings_actions_delegate_dnd_locking_to_dnd_handler():
    runtime = MagicMock()
    dnd = MagicMock()
    actions = SettingsActions(runtime=runtime, dnd=dnd)

    actions.set_icons_locked(True)

    dnd.set_locked.assert_called_once_with(True)
    runtime.set_icons_locked.assert_not_called()


def test_settings_actions_delegate_shell_actions_to_runtime():
    runtime = MagicMock()
    dnd = MagicMock()
    actions = SettingsActions(runtime=runtime, dnd=dnd)

    actions.reposition()
    actions.queue_draw()
    actions.set_theme("theme")
    actions.check_for_updates_now()

    runtime.reposition.assert_called_once_with()
    runtime.queue_draw.assert_called_once_with()
    runtime.set_theme.assert_called_once_with("theme")
    runtime.check_for_updates_now.assert_called_once_with()
