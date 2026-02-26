"""Tests for multi-position icon coordinate mapping.

The renderer maps a 1D main-axis position to (x, y) window coordinates
depending on dock position. These tests verify correctness for all 4
positions including hide offset and bounce.
"""

from docking.core.position import Position
from docking.ui.renderer import map_icon_position

CROSS = 120.0  # window cross-axis size
EDGE_PAD = 5.0  # edge padding
ICON = 48.0  # icon size
MAIN = 500.0  # main-axis position


class TestBottomPosition:
    def test_x_equals_main_pos(self):
        x, y = map_icon_position(Position.BOTTOM, MAIN, CROSS, EDGE_PAD, ICON)
        assert x == MAIN

    def test_y_near_bottom(self):
        # Icon sits near bottom: cross_size - edge_padding - icon_size
        x, y = map_icon_position(Position.BOTTOM, MAIN, CROSS, EDGE_PAD, ICON)
        assert y == CROSS - EDGE_PAD - ICON

    def test_hide_pushes_down(self):
        _, y_rest = map_icon_position(Position.BOTTOM, MAIN, CROSS, EDGE_PAD, ICON)
        _, y_hide = map_icon_position(
            Position.BOTTOM, MAIN, CROSS, EDGE_PAD, ICON, hide_cross=20.0
        )
        assert y_hide > y_rest  # further down = larger y

    def test_bounce_pushes_up(self):
        _, y_rest = map_icon_position(Position.BOTTOM, MAIN, CROSS, EDGE_PAD, ICON)
        _, y_bounce = map_icon_position(
            Position.BOTTOM, MAIN, CROSS, EDGE_PAD, ICON, bounce=10.0
        )
        assert y_bounce < y_rest  # upward = smaller y


class TestTopPosition:
    def test_x_equals_main_pos(self):
        x, y = map_icon_position(Position.TOP, MAIN, CROSS, EDGE_PAD, ICON)
        assert x == MAIN

    def test_y_near_top(self):
        x, y = map_icon_position(Position.TOP, MAIN, CROSS, EDGE_PAD, ICON)
        assert y == EDGE_PAD

    def test_hide_pushes_up(self):
        _, y_rest = map_icon_position(Position.TOP, MAIN, CROSS, EDGE_PAD, ICON)
        _, y_hide = map_icon_position(
            Position.TOP, MAIN, CROSS, EDGE_PAD, ICON, hide_cross=20.0
        )
        assert y_hide < y_rest  # toward top = smaller y

    def test_bounce_pushes_down(self):
        _, y_rest = map_icon_position(Position.TOP, MAIN, CROSS, EDGE_PAD, ICON)
        _, y_bounce = map_icon_position(
            Position.TOP, MAIN, CROSS, EDGE_PAD, ICON, bounce=10.0
        )
        assert y_bounce > y_rest  # away from edge = larger y


class TestLeftPosition:
    def test_y_equals_main_pos(self):
        x, y = map_icon_position(Position.LEFT, MAIN, CROSS, EDGE_PAD, ICON)
        assert y == MAIN

    def test_x_near_left(self):
        x, y = map_icon_position(Position.LEFT, MAIN, CROSS, EDGE_PAD, ICON)
        assert x == EDGE_PAD

    def test_hide_pushes_left(self):
        x_rest, _ = map_icon_position(Position.LEFT, MAIN, CROSS, EDGE_PAD, ICON)
        x_hide, _ = map_icon_position(
            Position.LEFT, MAIN, CROSS, EDGE_PAD, ICON, hide_cross=20.0
        )
        assert x_hide < x_rest

    def test_bounce_pushes_right(self):
        x_rest, _ = map_icon_position(Position.LEFT, MAIN, CROSS, EDGE_PAD, ICON)
        x_bounce, _ = map_icon_position(
            Position.LEFT, MAIN, CROSS, EDGE_PAD, ICON, bounce=10.0
        )
        assert x_bounce > x_rest


class TestRightPosition:
    def test_y_equals_main_pos(self):
        x, y = map_icon_position(Position.RIGHT, MAIN, CROSS, EDGE_PAD, ICON)
        assert y == MAIN

    def test_x_near_right(self):
        x, y = map_icon_position(Position.RIGHT, MAIN, CROSS, EDGE_PAD, ICON)
        assert x == CROSS - EDGE_PAD - ICON

    def test_hide_pushes_right(self):
        x_rest, _ = map_icon_position(Position.RIGHT, MAIN, CROSS, EDGE_PAD, ICON)
        x_hide, _ = map_icon_position(
            Position.RIGHT, MAIN, CROSS, EDGE_PAD, ICON, hide_cross=20.0
        )
        assert x_hide > x_rest

    def test_bounce_pushes_left(self):
        x_rest, _ = map_icon_position(Position.RIGHT, MAIN, CROSS, EDGE_PAD, ICON)
        x_bounce, _ = map_icon_position(
            Position.RIGHT, MAIN, CROSS, EDGE_PAD, ICON, bounce=10.0
        )
        assert x_bounce < x_rest


class TestSymmetry:
    """Bottom/right and top/left should be mirrors of each other."""

    def test_bottom_right_same_cross_offset(self):
        # Both place icon at cross_size - edge_padding - icon_size from edge
        _, y_bottom = map_icon_position(Position.BOTTOM, MAIN, CROSS, EDGE_PAD, ICON)
        x_right, _ = map_icon_position(Position.RIGHT, MAIN, CROSS, EDGE_PAD, ICON)
        assert y_bottom == x_right

    def test_top_left_same_cross_offset(self):
        _, y_top = map_icon_position(Position.TOP, MAIN, CROSS, EDGE_PAD, ICON)
        x_left, _ = map_icon_position(Position.LEFT, MAIN, CROSS, EDGE_PAD, ICON)
        assert y_top == x_left

    def test_no_hide_no_bounce_all_within_bounds(self):
        for pos in Position:
            x, y = map_icon_position(pos, MAIN, CROSS, EDGE_PAD, ICON)
            assert x >= 0
            assert y >= 0
            assert x + ICON <= CROSS or y + ICON <= CROSS
