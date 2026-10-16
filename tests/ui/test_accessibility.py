"""Exercise real GTK/ATK controls, including dispatch and model churn.

Run with xvfb-run to exercise GTK on headless machines. Assertions use ATK's
public interfaces, not only the Python vfunc implementations.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import gi
import pytest

gi.require_version("Atk", "1.0")
from gi.repository import Atk, Gdk, GLib, Gtk

from docking.core.items import DockItem
from docking.ui.accessibility import DockAccessibilityManager
from docking.ui.geometry import Rect


def drain():
    context = GLib.MainContext.default()
    for _ in range(100):
        if not context.pending():
            break
        context.iteration(False)


@pytest.fixture
def dock():
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display required (run under xvfb-run)")
    items = [DockItem("a.desktop", name="Alpha"), DockItem("b.desktop", name="Beta")]
    model = MagicMock()
    model.visible_items.side_effect = lambda: list(items)
    model.find_by_desktop_id.side_effect = lambda desktop_id: next(
        (item for item in items if item.desktop_id == desktop_id), None
    )
    model.get_applet.return_value = None
    window = Gtk.Window()
    window.set_default_size(300, 100)
    window.model = model
    window.autohide = SimpleNamespace(hide_offset=0.0, set_keyboard_active=MagicMock())
    window.drawing_area = Gtk.DrawingArea()
    window.accessibility_overlay = Gtk.Overlay()
    window.accessibility_overlay.add(window.drawing_area)
    window.add(window.accessibility_overlay)
    window.queue_redraw = window.drawing_area.queue_draw
    window._current_or_build_geometry_frame = lambda: SimpleNamespace(
        geometry_for_item=lambda item: (
            SimpleNamespace(hit_rect=Rect(items.index(item) * 60 + 10, 20, 48, 48))
            if item in items
            else None
        )
    )
    input_controller, menu = MagicMock(), MagicMock()
    menu.show_stack_for_accessibility.return_value = False
    manager = DockAccessibilityManager(window, input_controller, menu)
    manager.start()
    window.show_all()
    drain()
    yield SimpleNamespace(
        items=items,
        window=window,
        manager=manager,
        input=input_controller,
        menu=menu,
        model=model,
    )
    manager.stop()
    window.destroy()
    drain()


def test_native_tree_names_roles_and_order(dock):
    accessible = dock.manager.layer.get_accessible()
    assert accessible.get_role() == Atk.Role.TOOL_BAR
    assert accessible.get_n_accessible_children() == 2
    first = accessible.ref_accessible_child(0)
    assert first.get_name() == "Alpha"
    assert first.get_role() == Atk.Role.PUSH_BUTTON
    assert Atk.Action.get_n_actions(first) == 2
    assert Atk.Action.get_name(first, 0) == "activate"
    assert Atk.Action.get_name(first, 1) == "menu"
    assert first.ref_state_set().contains_state(Atk.StateType.FOCUSABLE)
    dock.items.reverse()
    dock.manager.sync()
    assert accessible.ref_accessible_child(1) is first
    assert accessible.ref_accessible_child(0).get_name() == "Beta"


def test_names_and_descriptions_update_without_replacing_objects(dock):
    item = dock.items[0]
    accessible = dock.manager.buttons[id(item)].get_accessible()
    events = []
    accessible.connect(
        "property-change", lambda obj, value: events.append(value.property_name)
    )
    item.name = "New title"
    item.is_running = True
    item.is_active = True
    item.is_urgent = True
    item.badge_visible = True
    item.badge_count = 3
    item.progress_visible = True
    item.progress = 0.5
    dock.manager.sync()
    assert accessible is dock.manager.buttons[id(item)].get_accessible()
    assert accessible.get_name() == "New title"
    assert "Notifications: 3" in accessible.get_description()
    assert "Progress: 50%" in accessible.get_description()
    assert "accessible-name" in events
    assert "accessible-description" in events


def test_atk_activation_and_stale_queued_action(dock):
    item = dock.items[0]
    accessible = dock.manager.buttons[id(item)].get_accessible()
    assert Atk.Action.do_action(accessible, 0)
    assert not Atk.Action.do_action(accessible, 2)
    drain()
    assert dock.input.activate_item.call_args.args[0] is item
    dock.input.reset_mock()
    assert Atk.Action.do_action(accessible, 0)
    dock.items.remove(item)
    dock.manager.sync()
    drain()
    dock.input.activate_item.assert_not_called()
    assert not Atk.Action.do_action(accessible, 0)
    assert accessible.ref_state_set().contains_state(Atk.StateType.DEFUNCT)


def test_atk_focus_opens_native_navigation_and_menu(dock):
    item = dock.items[1]
    accessible = dock.manager.buttons[id(item)].get_accessible()
    assert Atk.Component.grab_focus(accessible)
    drain()
    assert (
        dock.manager.navigator.get_focus() is dock.manager.navigation_buttons[id(item)]
    )
    dock.window.autohide.set_keyboard_active.assert_called_with(True)
    assert Atk.Action.do_action(accessible, 1)
    drain()
    assert dock.menu.show_for_accessibility.call_args.args[0] is item
    dock.manager.close_navigation()
    dock.window.autohide.set_keyboard_active.assert_called_with(False)


def test_navigation_reorder_removal_and_keys(dock):
    manager = dock.manager
    manager.focus(dock.items[1])
    selected = dock.items[1]
    dock.items.reverse()
    manager.sync()
    assert manager.focused_item is selected
    key = SimpleNamespace(keyval=Gdk.KEY_Down, state=0)
    assert manager._on_key_press(manager.navigator, key)
    assert manager.focused_item is dock.items[1]
    key.keyval = Gdk.KEY_Home
    manager._on_key_press(manager.navigator, key)
    assert manager.focused_item is selected
    dock.items.remove(selected)
    manager.sync()
    assert manager.focused_item is dock.items[0]
    key.keyval = Gdk.KEY_Escape
    assert manager._on_key_press(manager.navigator, key)
    assert manager.navigator is None


def test_empty_dock_and_cleanup_cancel_actions(dock):
    manager = dock.manager
    manager.request_action(dock.items[0])
    manager.stop()
    drain()
    dock.input.activate_item.assert_not_called()
    assert not manager.buttons
    dock.model.remove_change_listener.assert_called_once()
    dock.items.clear()
    manager.start()
    assert manager.focus()
    assert manager.navigator is not None
    assert not manager.navigation_buttons


def test_geometry_and_hidden_state(dock):
    manager = dock.manager
    button = manager.buttons[id(dock.items[0])]
    manager._on_draw(dock.window.drawing_area, None)
    assert button.get_size_request() == (48, 48)
    assert manager.layer.child_get_property(button, "x") == 10
    assert manager.layer.child_get_property(button, "y") == 20
    dock.window.autohide.hide_offset = 1.0
    manager._on_draw(dock.window.drawing_area, None)
    assert not button.get_visible()
    dock.window.autohide.hide_offset = 0.0
    manager._on_draw(dock.window.drawing_area, None)
    assert button.get_visible()
    assert dock.window.accessibility_overlay.get_overlay_pass_through(manager.layer)


def test_separators_and_removed_animation_ghosts_are_excluded(dock):
    ghost = DockItem("gone.desktop")
    separator = DockItem("applet://separator#2")
    dock.items.append(separator)
    dock.model.visible_items.side_effect = lambda: [*dock.items, ghost]
    dock.manager.sync()
    assert id(ghost) not in dock.manager.buttons
    assert id(separator) not in dock.manager.buttons


def test_native_stack_alternative_uses_the_same_entry_callbacks(dock):
    from docking.ui.menu import MenuHandler
    from docking.ui.stack import StackAction, StackContent, StackEntry

    activate, open_folder = MagicMock(), MagicMock()
    content = StackContent(
        entries=(StackEntry("document", "Document", None, activate),),
        action=StackAction("open", "Open folder", open_folder),
    )
    handler = object.__new__(MenuHandler)
    handler._model = dock.model
    handler._folder_stack = MagicMock()
    handler._folder_stack.content_for_accessibility.return_value = content
    popup = Gtk.Menu()
    handler._new_popup_menu = lambda: popup
    folder = DockItem("folder", kind="folder")
    dock.manager.focus()
    assert handler.show_stack_for_accessibility(folder, dock.manager.options_button)
    rows = popup.get_children()
    assert [row.get_label() for row in rows] == ["Open folder", "Document"]
    rows[1].activate()
    activate.assert_called_once_with()
    rows[0].activate()
    open_folder.assert_called_once_with()
    popup.destroy()


def test_options_context_key_does_not_target_previous_item(dock):
    dock.manager.focus(dock.items[0])
    dock.manager.options_button.grab_focus()
    event = SimpleNamespace(keyval=Gdk.KEY_Menu, state=0)
    assert dock.manager._on_key_press(dock.manager.navigator, event)
    assert dock.menu.show_for_accessibility.call_args.args[0] is None


def test_adjustable_applet_exposes_wheel_actions_without_losing_focus(dock):
    calls = []

    class Adjustable:
        def on_scroll(self, direction_up):
            calls.append(direction_up)

    item = dock.items[0]
    item.kind = "applet"
    applet = Adjustable()
    dock.model.get_applet.side_effect = lambda did: (
        applet if did == item.desktop_id else None
    )
    accessible = dock.manager.buttons[id(item)].get_accessible()
    assert Atk.Action.get_n_actions(accessible) == 4
    assert Atk.Action.get_name(accessible, 2) == "scroll-up"
    assert Atk.Action.get_name(accessible, 3) == "scroll-down"
    dock.manager.focus(item)
    assert Atk.Action.do_action(accessible, 2)
    drain()
    assert calls == [True]
    key = SimpleNamespace(keyval=Gdk.KEY_Page_Down, state=0)
    assert dock.manager._on_key_press(dock.manager.navigator, key)
    drain()
    assert calls == [True, False]
    assert dock.manager.focused_item is item
    assert dock.manager.navigator is not None
