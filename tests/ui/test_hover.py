"""Tests for hover manager."""

import sys
from unittest.mock import MagicMock

# Mock gi before importing
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.ui.hover import HoverManager, PREVIEW_SHOW_DELAY_MS  # noqa: E402


class TestHoverManagerInit:
    def test_initial_state(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        # When
        hover = HoverManager(window, config, model, theme, MagicMock())
        # Then
        assert hover.hovered_item is None
        assert hover._preview_timer_id == 0
        assert hover._anim_timer_id == 0

    def test_preview_show_delay_reasonable(self):
        # Given / When / Then
        assert 200 <= PREVIEW_SHOW_DELAY_MS <= 800


class TestHoverManagerPreview:
    def test_set_preview(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        hover = HoverManager(window, config, model, theme, MagicMock())
        preview = MagicMock()
        # When
        hover.set_preview(preview)
        # Then
        assert hover._preview is preview

    def test_tooltip_stored_from_constructor(self):
        # Given
        window = MagicMock()
        config = MagicMock()
        model = MagicMock()
        theme = MagicMock()
        tooltip = MagicMock()
        # When
        hover = HoverManager(window, config, model, theme, tooltip)
        # Then
        assert hover._tooltip is tooltip
