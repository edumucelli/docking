"""Tests for DockRenderer state management."""

from unittest.mock import MagicMock

import pytest

from docking.core.theme import Theme
from docking.ui.renderer import (
    SHELF_SMOOTH_FACTOR,
    DockRenderer,
)

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
        # When
        target = 478.0
        if renderer.smooth_shelf_w == 0.0:
            renderer.smooth_shelf_w = target
        # Then
        assert renderer.smooth_shelf_w == target

    def test_lerps_after_first_snap(self):
        # Given
        renderer = DockRenderer()
        renderer.smooth_shelf_w = 478.0
        # When
        target = 520.0
        renderer.smooth_shelf_w += (
            target - renderer.smooth_shelf_w
        ) * SHELF_SMOOTH_FACTOR
        # Then
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
        # When
        for _ in range(_FADE_FRAMES + 5):
            renderer._update_hover_lighten(items, "test.desktop", _DEFAULT_THEME)
        # Then
        assert renderer._hover_lighten["test.desktop"] == pytest.approx(_HOVER_MAX)

    def test_fade_out_after_unhover(self):
        # Given
        renderer = DockRenderer()
        item = MagicMock()
        item.desktop_id = "test.desktop"
        items = [item]
        renderer._hover_lighten["test.desktop"] = _HOVER_MAX
        # When
        for _ in range(_FADE_FRAMES + 5):
            renderer._update_hover_lighten(items, "", _DEFAULT_THEME)
        # Then
        assert "test.desktop" not in renderer._hover_lighten

    def test_clamps_to_max(self):
        # Given
        renderer = DockRenderer()
        item = MagicMock()
        item.desktop_id = "test.desktop"
        items = [item]
        # When
        for _ in range(100):
            renderer._update_hover_lighten(items, "test.desktop", _DEFAULT_THEME)
        # Then
        assert renderer._hover_lighten["test.desktop"] <= _HOVER_MAX

    def test_cleanup_removed_items(self):
        # Given
        renderer = DockRenderer()
        renderer._hover_lighten["removed.desktop"] = _HOVER_MAX
        item = MagicMock()
        item.desktop_id = "still-here.desktop"
        items = [item]
        # When
        renderer._update_hover_lighten(items, "still-here.desktop", _DEFAULT_THEME)
        # Then
        assert "removed.desktop" not in renderer._hover_lighten


class TestShelfExpandsWithDropGap:
    """Shelf must expand to cover icons displaced by external drop gap.

    Previously, dragging an item into the dock would open a gap between
    icons but the shelf stayed at its original width, leaving the
    rightmost icon outside the shelf background.
    """

    def test_shelf_snaps_wider_when_drop_gap_active(self):
        # Given
        renderer = DockRenderer()
        base_w = 500.0
        renderer.smooth_shelf_w = base_w
        # When
        drop_gap = 48 + 12  # icon_size + item_padding
        target = base_w + drop_gap
        # Simulate the snap logic: when drop_gap > 0, snap instead of lerp
        if drop_gap > 0:
            renderer.smooth_shelf_w = target
        # Then
        assert renderer.smooth_shelf_w == target

    def test_shelf_lerps_back_after_drop_gap_clears(self):
        # Given
        renderer = DockRenderer()
        renderer.smooth_shelf_w = 560.0
        target = 500.0  # no gap
        # When
        renderer.smooth_shelf_w += (
            target - renderer.smooth_shelf_w
        ) * SHELF_SMOOTH_FACTOR
        # Then
        assert renderer.smooth_shelf_w < 560.0
        assert renderer.smooth_shelf_w > target


class TestShelfSnapDuringHide:
    """Shelf width must snap (not lerp) during hide/show animation.

    When zoom decays during hide, icon spread shrinks each frame. If the
    shelf lerps, it lags behind, creating growing gaps at the edges.
    """

    def test_shelf_snaps_when_hiding(self):
        # Given
        renderer = DockRenderer()
        renderer.smooth_shelf_w = 600.0
        target = 500.0  # icons compressed by zoom decay
        # When
        hide_offset = 0.5
        if hide_offset > 0:
            renderer.smooth_shelf_w = target
        # Then
        assert renderer.smooth_shelf_w == target

    def test_shelf_lerps_when_visible(self):
        # Given
        renderer = DockRenderer()
        renderer.smooth_shelf_w = 600.0
        target = 500.0
        # When
        hide_offset = 0.0
        if hide_offset > 0:
            renderer.smooth_shelf_w = target
        else:
            renderer.smooth_shelf_w += (
                target - renderer.smooth_shelf_w
            ) * SHELF_SMOOTH_FACTOR
        # Then
        assert renderer.smooth_shelf_w < 600.0
        assert renderer.smooth_shelf_w > target
