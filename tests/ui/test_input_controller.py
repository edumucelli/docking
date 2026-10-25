"""Tests for DockInputController lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.position import Position
from docking.platform.model import DockItem
from docking.ui.geometry import Rect
from docking.ui.hover import HoverManager
from docking.ui.input_controller import DockInputController


@pytest.mark.parametrize("position", list(Position))
def test_enter_alone_sets_hover_and_tooltip_without_a_following_motion(position):
    item = DockItem("applet://battery", name="No battery")
    frame = SimpleNamespace(
        cursor_rect=Rect(0, 0, 100, 100),
        hover_item_at_point=MagicMock(return_value=item),
    )
    tooltip = MagicMock()
    window = SimpleNamespace(
        cursor_x=-1.0,
        cursor_y=-1.0,
        dock_hovered=False,
        config=SimpleNamespace(pos=position),
        autohide=SimpleNamespace(enabled=False),
        interaction=MagicMock(),
        _build_and_store_geometry_frame=MagicMock(return_value=frame),
    )
    window.interaction.on_effective_enter.side_effect = lambda: setattr(
        window, "dock_hovered", True
    )
    window.hover = HoverManager(
        window, window.config, MagicMock(), MagicMock(), tooltip, MagicMock()
    )
    controller = DockInputController(
        window=window,
        interactions=MagicMock(),
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )
    controller._on_enter(MagicMock(), SimpleNamespace(x=25, y=60))
    assert window.hover.hovered_item is item
    tooltip.update.assert_called_once_with(item, frame)
    assert (window.cursor_x, window.cursor_y) == (25, 60)


def test_motion_outside_dock_keeps_stack_open_for_pointer_transit():
    frame = SimpleNamespace(
        cursor_rect=Rect(0, 0, 100, 54),
        item_at_point=MagicMock(return_value=None),
    )
    window = SimpleNamespace(
        _build_and_store_geometry_frame=MagicMock(return_value=frame),
        update_input_region=MagicMock(),
        _schedule_redraw=MagicMock(),
        dock_hovered=False,
    )
    interactions = MagicMock()
    controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )

    controller._on_motion(MagicMock(), SimpleNamespace(x=50, y=80))

    interactions.close_stack_unless_target.assert_not_called()


class _SignalObject:
    def __init__(self) -> None:
        self.connected: list[tuple[str, object, int]] = []
        self.disconnected: list[int] = []
        self._next_handler_id = 1

    def connect(self, signal: str, callback) -> int:
        handler_id = self._next_handler_id
        self._next_handler_id += 1
        self.connected.append((signal, callback, handler_id))
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self.disconnected.append(handler_id)


def _window() -> SimpleNamespace:
    drawing_area = _SignalObject()
    window = _SignalObject()
    window.drawing_area = drawing_area
    window.model = SimpleNamespace(
        visible_items=MagicMock(return_value=["folder"]),
        add_change_listener=MagicMock(),
        remove_change_listener=MagicMock(),
        add_applet_change_listener=MagicMock(),
        remove_applet_change_listener=MagicMock(),
    )
    return window


def test_start_connects_signals_model_listener_and_prewarms_once():
    window = _window()
    interactions = MagicMock()
    controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )

    controller.start()
    controller.start()

    assert [signal for signal, _callback, _id in window.drawing_area.connected] == [
        "draw",
        "motion-notify-event",
        "button-press-event",
        "button-release-event",
        "leave-notify-event",
        "enter-notify-event",
        "scroll-event",
    ]
    assert [signal for signal, _callback, _id in window.connected] == ["destroy"]
    window.model.add_change_listener.assert_called_once_with(
        controller._on_model_changed
    )
    window.model.add_applet_change_listener.assert_called_once_with(
        controller._on_applet_changed
    )
    interactions.prewarm_visible_folder_stacks.assert_called_once_with(["folder"])


def test_stop_disconnects_signals_and_model_listener_once():
    window = _window()
    controller = DockInputController(
        window=window,
        interactions=MagicMock(),
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )

    controller.start()
    controller.stop()
    controller.stop()

    assert window.drawing_area.disconnected == [1, 2, 3, 4, 5, 6, 7]
    assert window.disconnected == [1]
    window.model.remove_change_listener.assert_called_once_with(
        controller._on_model_changed
    )
    window.model.remove_applet_change_listener.assert_called_once_with(
        controller._on_applet_changed
    )


def test_applet_change_refreshes_its_open_stack():
    applet = MagicMock()
    window = SimpleNamespace(
        model=SimpleNamespace(
            get_applet=MagicMock(return_value=applet),
        ),
    )
    interactions = MagicMock()
    interactions.open_stack_owner_id.return_value = "applet://devices"
    controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )

    controller._on_applet_changed("applet://devices")

    window.model.get_applet.assert_called_once_with("applet://devices")
    interactions.refresh_open_applet_stack.assert_called_once_with(applet)


def test_unrelated_applet_change_does_not_refresh_open_stack():
    window = SimpleNamespace(model=SimpleNamespace(get_applet=MagicMock()))
    interactions = MagicMock()
    interactions.open_stack_owner_id.return_value = "applet://devices"
    controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )

    controller._on_applet_changed("applet://clock")

    window.model.get_applet.assert_not_called()
    interactions.refresh_open_applet_stack.assert_not_called()


def test_model_change_closes_stack_for_removed_applet():
    window = SimpleNamespace(
        model=SimpleNamespace(
            visible_items=MagicMock(return_value=[]),
            get_applet=MagicMock(return_value=None),
        ),
        hover=SimpleNamespace(
            hovered_item=None,
            on_model_changed=MagicMock(),
        ),
        config=SimpleNamespace(pos="bottom"),
        placement=SimpleNamespace(set_struts=MagicMock()),
        _invalidate_current_geometry_frame=MagicMock(),
        update_input_region=MagicMock(),
        _schedule_redraw=MagicMock(),
    )
    interactions = MagicMock()
    interactions.open_stack_owner_id.return_value = "applet://devices"
    controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )

    controller._on_model_changed()

    interactions.refresh_open_applet_stack.assert_called_once_with(None)


def test_model_change_does_not_refresh_existing_applet_stack():
    applet = MagicMock()
    window = SimpleNamespace(
        model=SimpleNamespace(
            visible_items=MagicMock(return_value=[]),
            get_applet=MagicMock(return_value=applet),
        ),
        hover=SimpleNamespace(
            hovered_item=None,
            on_model_changed=MagicMock(),
        ),
        config=SimpleNamespace(pos="bottom"),
        placement=SimpleNamespace(set_struts=MagicMock()),
        _invalidate_current_geometry_frame=MagicMock(),
        update_input_region=MagicMock(),
        _schedule_redraw=MagicMock(),
    )
    interactions = MagicMock()
    interactions.open_stack_owner_id.return_value = "applet://devices"
    controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )

    controller._on_model_changed()

    interactions.refresh_open_applet_stack.assert_not_called()
