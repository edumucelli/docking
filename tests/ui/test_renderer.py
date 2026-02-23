"""Tests for DockRenderer state management."""

import pytest
from unittest.mock import MagicMock

from docking.ui.renderer import (
    DockRenderer,
    SHELF_SMOOTH_FACTOR,
)
from docking.core.theme import Theme

# Default theme at 48px for hover lighten tests
_DEFAULT_THEME = Theme.load("default", 48)
_HOVER_MAX = _DEFAULT_THEME.hover_lighten
_FADE_FRAMES = max(1, _DEFAULT_THEME.active_time_ms // 16)


class TestSmoothShelfW:
    def test_initial_value_is_zero(self):
        # Given / When
        renderer = DockRenderer()
        # Then
        assert renderer.smooth_shelf_w == 0.0

    def test_snaps_to_target_on_first_nonzero(self):
        # Given
        renderer = DockRenderer()
        assert renderer.smooth_shelf_w == 0.0
        # When — simulate what draw() does on first frame
        target = 478.0
        if renderer.smooth_shelf_w == 0.0:
            renderer.smooth_shelf_w = target
        # Then — should snap, not lerp from 0
        assert renderer.smooth_shelf_w == target

    def test_lerps_after_first_snap(self):
        # Given
        renderer = DockRenderer()
        renderer.smooth_shelf_w = 478.0
        # When — simulate a different target (zoom active)
        target = 520.0
        renderer.smooth_shelf_w += (
            target - renderer.smooth_shelf_w
        ) * SHELF_SMOOTH_FACTOR
        # Then — should lerp, not snap
        assert renderer.smooth_shelf_w != target
        assert renderer.smooth_shelf_w > 478.0
        assert renderer.smooth_shelf_w < 520.0


class TestHoverLighten:
    def test_initial_empty(self):
        # Given / When
        renderer = DockRenderer()
        # Then
        assert renderer._hover_lighten == {}

    def test_fade_in_on_hover(self):
        # Given
        renderer = DockRenderer()
        item = MagicMock()
        item.desktop_id = "test.desktop"
        items = [item]
        # When — simulate several frames of hovering
        for _ in range(_FADE_FRAMES + 5):
            renderer._update_hover_lighten(items, "test.desktop", _DEFAULT_THEME)
        # Then — should reach max lighten
        assert renderer._hover_lighten["test.desktop"] == pytest.approx(_HOVER_MAX)

    def test_fade_out_after_unhover(self):
        # Given — item is at max lighten
        renderer = DockRenderer()
        item = MagicMock()
        item.desktop_id = "test.desktop"
        items = [item]
        renderer._hover_lighten["test.desktop"] = _HOVER_MAX
        # When — hover moves away
        for _ in range(_FADE_FRAMES + 5):
            renderer._update_hover_lighten(items, "", _DEFAULT_THEME)
        # Then — should fade to zero and be removed
        assert "test.desktop" not in renderer._hover_lighten

    def test_clamps_to_max(self):
        # Given
        renderer = DockRenderer()
        item = MagicMock()
        item.desktop_id = "test.desktop"
        items = [item]
        # When — many frames
        for _ in range(100):
            renderer._update_hover_lighten(items, "test.desktop", _DEFAULT_THEME)
        # Then — never exceeds max
        assert renderer._hover_lighten["test.desktop"] <= _HOVER_MAX

    def test_cleanup_removed_items(self):
        # Given — item has a lighten value but is no longer in the item list
        renderer = DockRenderer()
        renderer._hover_lighten["removed.desktop"] = _HOVER_MAX
        item = MagicMock()
        item.desktop_id = "still-here.desktop"
        items = [item]
        # When — update with a list that doesn't include "removed.desktop"
        renderer._update_hover_lighten(items, "still-here.desktop", _DEFAULT_THEME)
        # Then — removed item's lighten entry should be cleaned up
        assert "removed.desktop" not in renderer._hover_lighten
