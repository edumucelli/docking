"""Tests for Position enum and helpers."""

from docking.core.position import Position, is_horizontal


class TestPositionEnum:
    def test_four_positions(self):
        assert len(Position) == 4

    def test_values_are_strings(self):
        for pos in Position:
            assert isinstance(pos.value, str)

    def test_roundtrip_from_string(self):
        for pos in Position:
            assert Position(pos.value) is pos


class TestIsHorizontal:
    def test_bottom_is_horizontal(self):
        assert is_horizontal(Position.BOTTOM) is True

    def test_top_is_horizontal(self):
        assert is_horizontal(Position.TOP) is True

    def test_left_is_vertical(self):
        assert is_horizontal(Position.LEFT) is False

    def test_right_is_vertical(self):
        assert is_horizontal(Position.RIGHT) is False
