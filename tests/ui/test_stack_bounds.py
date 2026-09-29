"""Bounds are monitor-local logical space, not an assumed primary-screen size."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.position import Position
from docking.platform.backends.base import Rect, Size
from docking.ui.stack import StackPopupController, stack_available_size


@pytest.mark.parametrize(
    "position,expected",
    [
        (Position.BOTTOM, Size(1280, 464)),
        (Position.TOP, Size(1280, 280)),
        (Position.LEFT, Size(112, 760)),
        (Position.RIGHT, Size(1152, 760)),
    ],
)
def test_space_faces_away_from_dock_on_offset_monitor(position, expected):
    assert (
        stack_available_size(Rect(-1280, 28, 1280, 760), -120, 500, position)
        == expected
    )


@pytest.mark.parametrize("position", list(Position))
def test_outdated_anchor_cannot_request_more_than_workarea(position):
    size = stack_available_size(Rect(100, 80, 300, 240), -1000, 3000, position)
    assert 1 <= size.width <= 300
    assert 1 <= size.height <= 240


def test_side_stack_clamps_before_wayland_parent_relative_conversion(monkeypatch):
    controller = StackPopupController(
        config=SimpleNamespace(pos=Position.LEFT),
        runtime=MagicMock(),
        dock_window=MagicMock(),
    )
    window = MagicMock()
    window.get_transient_for.return_value = SimpleNamespace(
        surface_service=SimpleNamespace(
            popups_use_parent_relative_coordinates=True,
            get_surface_position=lambda: (-1280, 0),
        )
    )
    revealer = MagicMock()
    revealer.get_child().get_preferred_size.return_value = (
        None,
        SimpleNamespace(width=477, height=800),
    )
    controller._folder_stack_window = window
    controller._folder_stack_revealer = revealer
    controller._folder_stack_anchor_x = -1175
    controller._folder_stack_anchor_y = 280
    monkeypatch.setattr(
        "docking.ui.stack.popup_workarea", lambda *_: Rect(-1280, 0, 1280, 800)
    )
    controller._position_stack_window()
    window.move.assert_called_once_with(113, 0)
