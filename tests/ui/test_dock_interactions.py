"""Tests for the DockInteractions coordinator."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.ui.dock_window as dock_window_mod
from docking.core.position import Position
from docking.ui.dock_interactions import DockInteractions, StackAnchor


def test_context_menu_uses_current_frame_and_closes_folder_stack():
    menu = MagicMock()
    folder_stack = MagicMock()
    interactions = DockInteractions(menu=menu, folder_stack=folder_stack)
    event = SimpleNamespace(x=12.0, y=8.0)
    frame = MagicMock()

    interactions.show_context_menu(
        event=event,
        cursor_main=12.0,
        frame=frame,
        force_background=True,
    )

    folder_stack.close.assert_called_once_with()
    menu.show.assert_called_once_with(
        event=event,
        cursor_main=12.0,
        frame=frame,
        force_background=True,
    )


def test_folder_stack_uses_anchor_value_object():
    menu = MagicMock()
    folder_stack = MagicMock()
    interactions = DockInteractions(menu=menu, folder_stack=folder_stack)
    item = MagicMock()
    anchor = StackAnchor(x=100, y=200, icon_w=48, position=Position.BOTTOM)

    interactions.show_folder_stack(
        item=item,
        anchor=anchor,
        toggle_if_same_item=False,
    )

    folder_stack.show.assert_called_once_with(
        item=item,
        anchor_x=100,
        anchor_y=200,
        icon_w=48,
        position=Position.BOTTOM,
        toggle_if_same_item=False,
    )


def test_folder_stack_closes_when_hover_leaves_source_item():
    menu = MagicMock()
    folder_stack = MagicMock()
    folder_stack.open_owner_id.return_value = "file:///tmp/docs"
    interactions = DockInteractions(menu=menu, folder_stack=folder_stack)

    interactions.close_stack_unless_target(
        SimpleNamespace(desktop_id="firefox.desktop")
    )

    folder_stack.close.assert_called_once_with()


def test_folder_stack_stays_open_while_hovering_source_item():
    menu = MagicMock()
    folder_stack = MagicMock()
    folder_stack.open_owner_id.return_value = "file:///tmp/docs"
    interactions = DockInteractions(menu=menu, folder_stack=folder_stack)

    interactions.close_stack_unless_target(
        SimpleNamespace(desktop_id="file:///tmp/docs")
    )

    folder_stack.close.assert_not_called()


def test_declarative_applet_stack_uses_shared_controller():
    folder_stack = MagicMock()
    folder_stack.show_applet_stack.return_value = True
    interactions = DockInteractions(menu=MagicMock(), folder_stack=folder_stack)
    applet = MagicMock()
    applet.item.desktop_id = "applet://devices"
    anchor = StackAnchor(x=100, y=200, icon_w=48, position=Position.BOTTOM)
    parent = MagicMock()

    assert (
        interactions.show_applet_stack(
            applet=applet,
            anchor=anchor,
            parent=parent,
        )
        is True
    )

    folder_stack.show_applet_stack.assert_called_once_with(
        owner_id="applet://devices",
        provider=applet.stack_content,
        anchor_x=100,
        anchor_y=200,
        icon_w=48,
        position=Position.BOTTOM,
        parent=parent,
        toggle_if_same_owner=True,
    )


def test_open_applet_stack_refreshes_from_latest_content():
    folder_stack = MagicMock()
    folder_stack.open_owner_id.return_value = "applet://devices"
    folder_stack.open_item_id.return_value = None
    interactions = DockInteractions(menu=MagicMock(), folder_stack=folder_stack)
    applet = MagicMock()
    applet.item.desktop_id = "applet://devices"

    interactions.refresh_open_applet_stack(applet)

    folder_stack.refresh.assert_called_once_with(owner_id="applet://devices")


def test_dock_window_does_not_import_menu_handler():
    source = inspect.getsource(dock_window_mod)

    assert "from docking.ui.menu import MenuHandler" not in source
    assert "MenuHandler(" not in source
