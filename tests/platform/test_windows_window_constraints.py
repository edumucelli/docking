"""Decorated frames fit the real native work area without taking focus."""

from unittest.mock import Mock

import pytest

from docking.platform.backends.base import Rect
from docking.platform.desktop.implementations.windows.geometry import constrain_window


def native(rect: Rect):
    def read(_window, output):
        output._obj.left, output._obj.top = rect.x, rect.y
        output._obj.right, output._obj.bottom = (
            rect.x + rect.width,
            rect.y + rect.height,
        )
        return True

    user = Mock()
    user.GetWindowRect.side_effect = read
    user.SetWindowPos.return_value = True
    return user


@pytest.mark.parametrize(
    "frame,area,expected",
    [
        (Rect(90, 0, 1152, 700), Rect(0, 0, 1280, 681), Rect(90, 0, 1152, 681)),
        (
            Rect(-2100, -100, 1200, 800),
            Rect(-1920, 0, 1920, 1040),
            Rect(-1920, 0, 1200, 800),
        ),
        (
            Rect(2700, 900, 400, 300),
            Rect(1920, 0, 1280, 720),
            Rect(2700, 420, 400, 300),
        ),
        (Rect(0, 0, 2200, 1600), Rect(0, 0, 1280, 610), Rect(0, 0, 1280, 610)),
    ],
)
def test_real_frame_is_clamped_in_native_pixels(frame, area, expected):
    user = native(frame)
    assert constrain_window(42, area, user)
    user.SetWindowPos.assert_called_once_with(
        42, None, expected.x, expected.y, expected.width, expected.height, 0x14
    )


def test_already_fitting_frame_is_not_moved_or_reactivated():
    user = native(Rect(50, 30, 900, 600))
    assert not constrain_window(42, Rect(0, 0, 1280, 681), user)
    user.SetWindowPos.assert_not_called()


def test_missing_workarea_does_not_issue_a_native_move():
    user = native(Rect(0, 0, 100, 100))
    with pytest.raises(OSError, match="work area"):
        constrain_window(42, Rect(0, 0, 0, 0), user)
    user.GetWindowRect.assert_not_called()
