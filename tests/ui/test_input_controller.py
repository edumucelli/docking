"""Tests for DockInputController lifecycle."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.ui.input_controller import DockInputController


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
    )

    controller._on_model_changed()

    interactions.refresh_open_applet_stack.assert_not_called()
