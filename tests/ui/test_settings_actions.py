"""Tests for preferences action delegation."""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.ui.settings import SettingsActions


def test_settings_actions_delegate_dnd_locking_to_dnd_handler():
    runtime = MagicMock()
    dnd = MagicMock()
    actions = SettingsActions(runtime=runtime, dnd=dnd, model=MagicMock())

    actions.set_icons_locked(True)

    dnd.set_locked.assert_called_once_with(True)
    runtime.set_icons_locked.assert_not_called()


def test_settings_actions_delegate_shell_actions_to_runtime():
    runtime = MagicMock()
    dnd = MagicMock()
    actions = SettingsActions(runtime=runtime, dnd=dnd, model=MagicMock())

    actions.reposition()
    actions.queue_draw()
    actions.set_theme("theme")
    actions.check_for_updates_now()

    runtime.reposition.assert_called_once_with()
    runtime.queue_draw.assert_called_once_with()
    runtime.set_theme.assert_called_once_with("theme")
    runtime.check_for_updates_now.assert_called_once_with()


def test_settings_actions_delegate_all_remaining_to_runtime():
    runtime = MagicMock()
    dnd = MagicMock()
    actions = SettingsActions(runtime=runtime, dnd=dnd, model=MagicMock())

    actions.on_hide_mode_changed()
    actions.set_active_display(True)
    actions.refresh_pressure_handler()
    actions.set_current_workspace_only(False)
    actions.hide_tooltip()
    actions.open_releases_page()

    runtime.on_hide_mode_changed.assert_called_once_with()
    runtime.set_active_display.assert_called_once_with(True)
    runtime.refresh_pressure_handler.assert_called_once_with()
    runtime.set_current_workspace_only.assert_called_once_with(False)
    runtime.hide_tooltip.assert_called_once_with()
    runtime.open_releases_page.assert_called_once_with()


def test_settings_actions_get_monitor_choices():
    runtime = MagicMock()
    runtime.get_monitor_choices.return_value = ["mon1", "mon2"]
    dnd = MagicMock()
    actions = SettingsActions(runtime=runtime, dnd=dnd, model=MagicMock())

    result = actions.get_monitor_choices()

    assert result == ["mon1", "mon2"]
    runtime.get_monitor_choices.assert_called_once_with()


def test_settings_actions_current_monitor_choice():
    runtime = MagicMock()
    runtime.current_monitor_choice.return_value = 2
    dnd = MagicMock()
    actions = SettingsActions(runtime=runtime, dnd=dnd, model=MagicMock())

    result = actions.current_monitor_choice()

    assert result == 2
    runtime.current_monitor_choice.assert_called_once_with()


def test_settings_actions_reconcile_launcher_overlay_visibility():
    runtime = MagicMock()
    dnd = MagicMock()
    model = MagicMock()
    actions = SettingsActions(runtime=runtime, dnd=dnd, model=model)

    actions.refresh_launcher_overlay_visibility()

    model.refresh_launcher_overlay_visibility.assert_called_once_with()


def test_settings_actions_clear_search_learning() -> None:
    search = MagicMock()
    actions = SettingsActions(
        runtime=MagicMock(),
        dnd=MagicMock(),
        model=MagicMock(),
        search=search,
    )

    actions.clear_search_learning()

    search.clear_learned_ranking.assert_called_once_with()
