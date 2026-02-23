"""Tests for tooltip manager."""

import sys
from unittest.mock import MagicMock

# Mock gi before importing
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.ui.tooltip import TooltipManager  # noqa: E402


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
        # When — item has no name
        tooltip.update(item, [])
        # Then — tooltip should be hidden
        tooltip._tooltip_window.hide.assert_called_once()
