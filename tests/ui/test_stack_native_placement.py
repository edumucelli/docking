"""Native popup constraints must use parent-local anchors and releasable sizes."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import Gdk, GLib

from docking.core.position import Position
from docking.platform.backends.base import Rect, Size
from docking.ui.stack import StackPopupController


@pytest.mark.parametrize(
    "edge,anchor,expected,gravity",
    [
        (Position.BOTTOM, (-640, 700), (440, 1017), Gdk.Gravity.SOUTH_WEST),
        (Position.TOP, (-640, 100), (440, 433), Gdk.Gravity.NORTH_WEST),
        (Position.LEFT, (-1175, 400), (113, 425), Gdk.Gravity.NORTH_WEST),
        (Position.RIGHT, (-105, 400), (1167, 425), Gdk.Gravity.NORTH_EAST),
    ],
)
def test_compositor_anchors_use_parent_coordinates(
    edge, anchor, expected, gravity, monkeypatch
):
    surface = SimpleNamespace(
        popups_use_parent_relative_coordinates=True,
        get_surface_position=lambda: (-1280, -325),
    )
    parent = SimpleNamespace(surface_service=surface)
    controller = StackPopupController(
        config=SimpleNamespace(pos=edge), runtime=MagicMock(), dock_window=parent
    )
    window = MagicMock()
    window.get_transient_for.return_value = parent
    revealer = MagicMock()
    revealer.get_child().get_preferred_size.return_value = (
        None,
        SimpleNamespace(width=400, height=600),
    )
    controller._folder_stack_window = window
    controller._folder_stack_revealer = revealer
    controller._folder_stack_popup_size = Size(400, 600)
    controller._folder_stack_anchor_x, controller._folder_stack_anchor_y = anchor
    controller._folder_stack_fold_center_x = 200
    monkeypatch.setattr(
        "docking.ui.stack.popup_workarea", lambda *_: Rect(-1280, 0, 1280, 800)
    )
    controller._position_stack_window()
    window.set_resizable.assert_called_with(True)
    window.realize.assert_called_once()
    args = window.get_window().move_to_rect.call_args.args
    rect, rect_gravity, window_gravity, hints, dx, dy = args
    assert (rect.x, rect.y, rect.width, rect.height) == (*expected, 1, 1)
    assert rect_gravity == Gdk.Gravity.NORTH_WEST
    assert window_gravity == gravity
    assert hints == Gdk.AnchorHints.RESIZE_X | Gdk.AnchorHints.RESIZE_Y
    assert (dx, dy) == (0, 0)
    window.move.assert_not_called()


@pytest.fixture
def constrained_stack(monkeypatch):
    surface = SimpleNamespace(popups_use_parent_relative_coordinates=True)
    controller = StackPopupController(
        config=SimpleNamespace(pos=Position.TOP),
        runtime=MagicMock(),
        dock_window=SimpleNamespace(surface_service=surface),
    )
    window = MagicMock()
    window.get_visible.return_value = True
    window.get_size.return_value = (475, 751)
    controller._folder_stack_window = window
    controller._folder_stack_popup_size = Size(475, 687)
    monkeypatch.setattr(controller, "_position_stack_window", MagicMock())
    return controller, window


def test_older_compositor_growth_is_retried_without_closing_stack(constrained_stack):
    controller, window = constrained_stack
    controller._folder_stack_constraint_source = 123
    assert controller._constrain_stack_size() == GLib.SOURCE_REMOVE
    assert controller._folder_stack_popup_size == Size(475, 623)
    assert controller._folder_stack_constraint_source == 0
    assert controller._folder_stack_constraint_retries == 1
    window.hide.assert_called_once()
    controller._position_stack_window.assert_called_once()
    window.show_all.assert_called_once()
    controller._runtime.menu_popup_closed.assert_not_called()


@pytest.mark.parametrize("visible,size", [(False, (475, 751)), (True, (475, 623))])
def test_queued_configure_rechecks_visibility_and_actual_size(
    constrained_stack, visible, size
):
    controller, window = constrained_stack
    window.get_visible.return_value = visible
    window.get_size.return_value = size
    assert controller._constrain_stack_size() == GLib.SOURCE_REMOVE
    assert controller._folder_stack_popup_size == Size(475, 687)
    window.hide.assert_not_called()


def test_growing_configures_are_coalesced_and_retries_bounded(
    constrained_stack, monkeypatch
):
    controller, window = constrained_stack
    schedule = MagicMock(return_value=123)
    monkeypatch.setattr(GLib, "idle_add", schedule)
    event = SimpleNamespace(width=475, height=751)
    controller._on_stack_configure(window, event)
    controller._on_stack_configure(window, event)
    schedule.assert_called_once_with(controller._constrain_stack_size)
    controller._folder_stack_constraint_source = 0
    controller._folder_stack_constraint_retries = 2
    controller._on_stack_configure(window, event)
    assert schedule.call_count == 1


def test_closing_stack_cancels_pending_constraint(constrained_stack, monkeypatch):
    controller, _window = constrained_stack
    remove = MagicMock()
    monkeypatch.setattr(GLib, "source_remove", remove)
    controller._folder_stack_constraint_source = 123
    controller.close()
    remove.assert_called_once_with(123)
    assert controller._folder_stack_popup_size is None
    assert controller._folder_stack_constraint_source == 0


def test_x11_configure_does_not_remap(constrained_stack, monkeypatch):
    controller, window = constrained_stack
    controller._dock_window.surface_service.popups_use_parent_relative_coordinates = (
        False
    )
    schedule = MagicMock()
    monkeypatch.setattr(GLib, "idle_add", schedule)
    controller._on_stack_configure(window, SimpleNamespace(width=500, height=800))
    schedule.assert_not_called()
