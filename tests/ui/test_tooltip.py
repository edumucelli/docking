"""Tests for tooltip manager."""

import sys
from unittest.mock import MagicMock

# Mock gi before importing
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.core.position import Position  # noqa: E402
from docking.ui.tooltip import (  # noqa: E402
    TooltipManager,
    TOOLTIP_GAP,
    compute_tooltip_position,
)


class TestTooltipManagerInit:
    def test_initial_state(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        # When
        tooltip = TooltipManager(window, config, model, theme)
        # Then
        assert tooltip._tooltip_window is None

    def test_gap_constant_reasonable(self):
        # Given
        from docking.ui.tooltip import TOOLTIP_GAP

        # When / Then — gap should be small positive value
        assert 5 <= TOOLTIP_GAP <= 20


class TestTooltipHide:
    def test_hide_when_no_window(self):
        # Given — tooltip window not yet created
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        # When / Then — should not raise
        tooltip.hide()

    def test_update_with_no_item_hides(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        tooltip._tooltip_window = MagicMock()
        # When — update with None item
        tooltip.update(None, [])
        # Then — tooltip should be hidden
        tooltip._tooltip_window.hide.assert_called_once()

    def test_update_with_unnamed_item_hides(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = TooltipManager(window, config, model, theme)
        tooltip._tooltip_window = MagicMock()
        item = MagicMock()
        item.name = ""
        # When -- item has no name
        tooltip.update(item, [])
        # Then -- tooltip should be hidden
        tooltip._tooltip_window.hide.assert_called_once()


# Anchor point for tests
AX, AY = 500.0, 300.0
TW, TH = 80, 24


class TestTooltipPositionBottom:
    def test_centered_horizontally(self):
        tx, ty = compute_tooltip_position(Position.BOTTOM, AX, AY, TW, TH)
        assert tx == int(AX - TW / 2)

    def test_above_anchor(self):
        tx, ty = compute_tooltip_position(Position.BOTTOM, AX, AY, TW, TH)
        assert ty == int(AY - TH - TOOLTIP_GAP)
        assert ty < AY


class TestTooltipPositionTop:
    def test_centered_horizontally(self):
        tx, ty = compute_tooltip_position(Position.TOP, AX, AY, TW, TH)
        assert tx == int(AX - TW / 2)

    def test_below_anchor(self):
        tx, ty = compute_tooltip_position(Position.TOP, AX, AY, TW, TH)
        assert ty == int(AY + TOOLTIP_GAP)
        assert ty > AY


class TestTooltipPositionLeft:
    def test_right_of_anchor(self):
        tx, ty = compute_tooltip_position(Position.LEFT, AX, AY, TW, TH)
        assert tx == int(AX + TOOLTIP_GAP)
        assert tx > AX

    def test_centered_vertically(self):
        tx, ty = compute_tooltip_position(Position.LEFT, AX, AY, TW, TH)
        assert ty == int(AY - TH / 2)


class TestTooltipPositionRight:
    def test_left_of_anchor(self):
        tx, ty = compute_tooltip_position(Position.RIGHT, AX, AY, TW, TH)
        assert tx == int(AX - TW - TOOLTIP_GAP)
        assert tx < AX

    def test_centered_vertically(self):
        tx, ty = compute_tooltip_position(Position.RIGHT, AX, AY, TW, TH)
        assert ty == int(AY - TH / 2)


class TestTooltipDirection:
    """Tooltip should always appear on the inner side (away from screen edge)."""

    def test_bottom_tooltip_above(self):
        _, ty = compute_tooltip_position(Position.BOTTOM, AX, AY, TW, TH)
        assert ty + TH <= AY  # tooltip bottom <= anchor

    def test_top_tooltip_below(self):
        _, ty = compute_tooltip_position(Position.TOP, AX, AY, TW, TH)
        assert ty >= AY  # tooltip top >= anchor

    def test_left_tooltip_right(self):
        tx, _ = compute_tooltip_position(Position.LEFT, AX, AY, TW, TH)
        assert tx >= AX  # tooltip left >= anchor

    def test_right_tooltip_left(self):
        tx, _ = compute_tooltip_position(Position.RIGHT, AX, AY, TW, TH)
        assert tx + TW <= AX  # tooltip right <= anchor
