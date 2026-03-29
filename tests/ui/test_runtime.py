"""Tests for DockRuntime command routing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

from docking.core.theme import Theme
from docking.ui.runtime import DockRuntime


def _autohide(*, enabled: bool = True):
    return SimpleNamespace(
        enabled=enabled,
        reset=MagicMock(),
        set_disabled=MagicMock(),
        set_hovered=MagicMock(),
        on_mouse_enter=MagicMock(),
        on_mouse_leave=MagicMock(),
    )


def _make_window():
    pointer = MagicMock()
    pointer.get_position.return_value = (None, 101, 202)
    seat = MagicMock()
    seat.get_pointer.return_value = pointer
    display = MagicMock()
    display.get_default_seat.return_value = seat
    return SimpleNamespace(
        interaction=SimpleNamespace(
            menu_popup_opened=MagicMock(),
            menu_popup_closed=MagicMock(),
        ),
        placement=SimpleNamespace(
            update_struts=MagicMock(),
            get_monitor_menu_choices=MagicMock(return_value=[("Display 1", 0)]),
            current_monitor_choice=MagicMock(return_value=1),
            primary_monitor_index=MagicMock(return_value=0),
            reposition=MagicMock(),
            start_active_display=MagicMock(),
            stop_active_display=MagicMock(),
        ),
        autohide=_autohide(),
        dnd=SimpleNamespace(set_locked=MagicMock()),
        tooltip=SimpleNamespace(hide=MagicMock()),
        preview=SimpleNamespace(hide=MagicMock()),
        theme="old-theme",
        cursor_x=12.0,
        cursor_y=34.0,
        is_pointer_inside_dock=MagicMock(return_value=True),
        get_display=MagicMock(return_value=display),
        get_position=MagicMock(return_value=(9, 7)),
        get_size=MagicMock(return_value=(320, 88)),
        on_hide_mode_changed=MagicMock(),
        queue_redraw=MagicMock(),
    )


class TestDockRuntime:
    def test_menu_popup_hooks_delegate_to_interaction(self):
        window = _make_window()
        runtime = DockRuntime(window)

        runtime.menu_popup_opened()
        runtime.menu_popup_closed()

        window.interaction.menu_popup_opened.assert_called_once()
        window.interaction.menu_popup_closed.assert_called_once()

    def test_placement_commands_delegate_to_placement(self):
        window = _make_window()
        runtime = DockRuntime(window)

        assert runtime.get_monitor_menu_choices() == [("Display 1", 0)]
        assert runtime.current_monitor_choice() == 1
        assert runtime.primary_monitor_index() == 0
        runtime.reposition()
        runtime.set_active_display(True)
        runtime.set_active_display(False)

        window.placement.get_monitor_menu_choices.assert_called_once()
        window.placement.current_monitor_choice.assert_called_once()
        window.placement.primary_monitor_index.assert_called_once()
        window.placement.reposition.assert_called_once()
        window.placement.start_active_display.assert_called_once()
        window.placement.stop_active_display.assert_called_once()

    def test_ui_commands_delegate_to_window_subsystems(self):
        window = _make_window()
        runtime = DockRuntime(window)

        runtime.on_hide_mode_changed()
        runtime.set_icons_locked(True)
        runtime.queue_draw()
        runtime.hide_tooltip()
        runtime.hide_hover_ui()
        runtime.set_theme(cast(Theme, "new-theme"))

        window.on_hide_mode_changed.assert_called_once()
        window.dnd.set_locked.assert_called_once_with(True)
        window.queue_redraw.assert_called_once()
        assert window.tooltip.hide.call_count == 2
        window.preview.hide.assert_called_once()
        assert window.theme == "new-theme"

    def test_cursor_position_returns_window_cursor(self):
        window = _make_window()
        runtime = DockRuntime(window)

        assert runtime.cursor_position() == (12.0, 34.0)
