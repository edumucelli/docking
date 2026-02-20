"""Tests for parabolic zoom math."""

import pytest
from unittest.mock import MagicMock

from docking.core.zoom import (
    compute_icon_zoom,
    compute_layout,
    total_width,
    content_bounds,
    LayoutItem,
)


class TestComputeIconZoom:
    def test_cursor_off_dock_returns_no_zoom(self):
        # Given / When
        result = compute_icon_zoom(-1.0, 100.0, 48, 2.0, 3)
        # Then
        assert result == 1.0

    def test_cursor_directly_on_icon_returns_max_zoom(self):
        # Given / When
        result = compute_icon_zoom(100.0, 100.0, 48, 2.0, 3)
        # Then
        assert result == pytest.approx(2.0)

    def test_cursor_at_max_range_returns_no_zoom(self):
        # Given
        icon_size, zoom_range = 48, 3
        max_dist = icon_size * zoom_range  # 144
        # When
        result = compute_icon_zoom(100.0 + max_dist, 100.0, icon_size, 2.0, zoom_range)
        # Then
        assert result == pytest.approx(1.0)

    def test_cursor_beyond_max_range_clamps(self):
        # Given / When
        result = compute_icon_zoom(500.0, 100.0, 48, 2.0, 3)
        # Then
        assert result == pytest.approx(1.0)

    def test_half_distance_gives_intermediate_zoom(self):
        # Given
        icon_size, zoom_range = 48, 3
        half_dist = (icon_size * zoom_range) / 2
        # When
        result = compute_icon_zoom(100.0 + half_dist, 100.0, icon_size, 2.0, zoom_range)
        # Then — at half distance: offset_pct=0.5, zoom=1-0.25=0.75, scale=1+0.75*1=1.75
        assert result == pytest.approx(1.75)

    def test_zoom_percent_of_1_always_returns_1(self):
        # Given / When
        result = compute_icon_zoom(100.0, 100.0, 48, 1.0, 3)
        # Then
        assert result == pytest.approx(1.0)

    def test_symmetry(self):
        """Zoom should be same distance left or right of center."""
        # Given / When
        left = compute_icon_zoom(80.0, 100.0, 48, 2.0, 3)
        right = compute_icon_zoom(120.0, 100.0, 48, 2.0, 3)
        # Then
        assert left == pytest.approx(right)


class TestComputeLayout:
    def _make_items(self, n: int) -> list:
        return [MagicMock() for _ in range(n)]

    def _make_config(
        self, icon_size=48, zoom_enabled=True, zoom_percent=2.0, zoom_range=3
    ):
        config = MagicMock()
        config.icon_size = icon_size
        config.zoom_enabled = zoom_enabled
        config.zoom_percent = zoom_percent
        config.zoom_range = zoom_range
        return config

    def test_empty_items_returns_empty(self):
        # Given
        config = self._make_config()
        # When / Then
        assert compute_layout([], config, 100.0) == []

    def test_no_zoom_uniform_scales(self):
        # Given
        items = self._make_items(3)
        config = self._make_config(zoom_enabled=False)
        # When
        layout = compute_layout(items, config, 100.0, item_padding=6, h_padding=12)
        # Then
        for li in layout:
            assert li.scale == pytest.approx(1.0)

    def test_cursor_off_dock_no_zoom(self):
        # Given
        items = self._make_items(3)
        config = self._make_config()
        # When
        layout = compute_layout(items, config, -1.0, item_padding=6, h_padding=12)
        # Then
        for li in layout:
            assert li.scale == pytest.approx(1.0)

    def test_layout_x_positions_monotonically_increase(self):
        # Given
        items = self._make_items(5)
        config = self._make_config()
        # When
        layout = compute_layout(items, config, 100.0, item_padding=6, h_padding=12)
        # Then
        for i in range(1, len(layout)):
            assert layout[i].x > layout[i - 1].x

    def test_zoomed_layout_wider_than_unzoomed(self):
        # Given
        items = self._make_items(5)
        config = self._make_config()
        # When
        layout_zoomed = compute_layout(
            items, config, 100.0, item_padding=6, h_padding=12
        )
        layout_flat = compute_layout(items, config, -1.0, item_padding=6, h_padding=12)
        w_zoomed = total_width(layout_zoomed, 48, 12)
        w_flat = total_width(layout_flat, 48, 12)
        # Then
        assert w_zoomed >= w_flat

    def test_single_item(self):
        # Given
        items = self._make_items(1)
        config = self._make_config()
        # When
        layout = compute_layout(items, config, -1.0, item_padding=6, h_padding=12)
        # Then
        assert len(layout) == 1
        assert layout[0].x == pytest.approx(12.0)
        assert layout[0].scale == pytest.approx(1.0)


class TestTotalWidth:
    def test_empty_layout(self):
        # Given / When
        result = total_width([], 48, 12)
        # Then
        assert result == pytest.approx(24.0)

    def test_single_item_no_zoom(self):
        # Given
        layout = [LayoutItem(x=12.0, scale=1.0)]
        # When
        result = total_width(layout, 48, 12)
        # Then — 12 + 48*1 + 12 = 72
        assert result == pytest.approx(72.0)

    def test_width_increases_with_zoom(self):
        # Given
        flat = [LayoutItem(x=12.0, scale=1.0), LayoutItem(x=66.0, scale=1.0)]
        zoomed = [LayoutItem(x=12.0, scale=1.5), LayoutItem(x=84.0, scale=1.5)]
        # When / Then
        assert total_width(zoomed, 48, 12) > total_width(flat, 48, 12)


class TestCenteringOffset:
    """Verify content centering math for fixed-size window."""

    def _make_config(self, zoom_enabled=True, zoom_percent=1.3):
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = zoom_enabled
        config.zoom_percent = zoom_percent
        config.zoom_range = 3
        return config

    def test_no_zoom_width_equals_base(self):
        """Without zoom, content width = base width."""
        # Given
        items = [MagicMock() for _ in range(5)]
        config = self._make_config(zoom_enabled=False)
        # When
        layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        w = total_width(layout, 48, 12)
        # Then
        expected = 12 + 5 * 48 + 4 * 10 + 12  # h_pad + icons + gaps + h_pad
        assert w == pytest.approx(expected)

    def test_zoomed_width_larger_than_base(self):
        # Given
        items = [MagicMock() for _ in range(5)]
        config = self._make_config()
        base_layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        base_w = total_width(base_layout, 48, 12)
        # When
        zoomed_layout = compute_layout(
            items, config, 150.0, item_padding=10, h_padding=12
        )
        zoomed_w = total_width(zoomed_layout, 48, 12)
        # Then
        assert zoomed_w > base_w

    def test_center_hover_is_widest(self):
        """Hovering at center produces the widest layout."""
        # Given
        items = [MagicMock() for _ in range(5)]
        config = self._make_config()
        base_layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        base_w = total_width(base_layout, 48, 12)
        center = base_w / 2
        # When
        center_layout = compute_layout(
            items, config, center, item_padding=10, h_padding=12
        )
        center_w = total_width(center_layout, 48, 12)
        edge_layout = compute_layout(items, config, 12.0, item_padding=10, h_padding=12)
        edge_w = total_width(edge_layout, 48, 12)
        # Then
        assert center_w >= edge_w


class TestPlankDisplacement:
    """Tests for Plank-style per-icon displacement (no cascading shifts)."""

    def _make_config(self, zoom_percent=1.5, zoom_range=3):
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = zoom_percent
        config.zoom_range = zoom_range
        return config

    def test_far_icons_have_constant_displacement(self):
        """Icons beyond zoom range all get the same small fixed displacement."""
        # Given
        items = [MagicMock() for _ in range(8)]
        config = self._make_config()
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When
        hover_left = compute_layout(items, config, 36.0, item_padding=10, h_padding=12)
        # Then — far icons should all have scale=1.0
        for i in range(5, 8):
            assert hover_left[i].scale == pytest.approx(1.0, abs=0.01)
        # Far icons should all have the SAME displacement (constant, not cascading)
        displacements = [hover_left[i].x - rest[i].x for i in range(5, 8)]
        for d in displacements:
            assert d == pytest.approx(displacements[0], abs=0.1)

    def test_hover_right_left_icons_shift_uniformly(self):
        """Hovering right icon — left icons shift left by a constant amount."""
        # Given
        items = [MagicMock() for _ in range(8)]
        config = self._make_config()
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        last_center = rest[-1].x + 24
        # When
        hover_right = compute_layout(
            items, config, last_center, item_padding=10, h_padding=12
        )
        # Then — far-left icons should all shift by the same constant amount
        displacements = [hover_right[i].x - rest[i].x for i in range(3)]
        for d in displacements:
            assert d == pytest.approx(displacements[0], abs=0.1)

    def test_icons_push_away_from_cursor(self):
        """Icons near cursor should be pushed away (left goes left, right goes right)."""
        # Given
        items = [MagicMock() for _ in range(5)]
        config = self._make_config()
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        center_x = rest[2].x + 24
        # When
        zoomed = compute_layout(items, config, center_x, item_padding=10, h_padding=12)
        # Then — icons left of cursor should shift left (smaller x)
        assert zoomed[0].x <= rest[0].x
        assert zoomed[1].x <= rest[1].x
        # Icons right of cursor should shift right (larger x)
        assert zoomed[3].x >= rest[3].x
        assert zoomed[4].x >= rest[4].x

    def test_hovered_icon_scales_up(self):
        """The icon directly under cursor should have max zoom."""
        # Given
        items = [MagicMock() for _ in range(5)]
        config = self._make_config()
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        center_x = rest[2].x + 24
        # When
        zoomed = compute_layout(items, config, center_x, item_padding=10, h_padding=12)
        # Then
        assert zoomed[2].scale == pytest.approx(1.5, abs=0.05)


class TestContentBounds:
    def test_no_layout_returns_zero_and_padding(self):
        # Given / When
        left, right = content_bounds([], 48, 12)
        # Then
        assert left == 0.0
        assert right == pytest.approx(24.0)

    def test_rest_layout_bounds(self):
        """Rest layout should have left edge at 0 and right edge at base_w."""
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = False
        config.zoom_percent = 1.0
        config.zoom_range = 3
        items = [MagicMock() for _ in range(3)]
        layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When
        left, right = content_bounds(layout, 48, 12)
        # Then
        expected_w = 12 * 2 + 3 * 48 + 2 * 10
        assert left == pytest.approx(0.0)
        assert right == pytest.approx(expected_w)

    def test_zoomed_bounds_wider_than_rest(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        rest_l, rest_r = content_bounds(rest, 48, 12)
        # When
        zoomed = compute_layout(items, config, 150.0, item_padding=10, h_padding=12)
        zoom_l, zoom_r = content_bounds(zoomed, 48, 12)
        # Then
        assert (zoom_r - zoom_l) >= (rest_r - rest_l)

    def test_left_edge_can_go_negative_during_zoom(self):
        """When hovering right side, left icons displace left — left_edge may be < 0."""
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When
        zoomed = compute_layout(
            items, config, rest[-1].x + 24, item_padding=10, h_padding=12
        )
        left, _ = content_bounds(zoomed, 48, 12)
        # Then — left edge should be at or below the rest left edge
        rest_left, _ = content_bounds(rest, 48, 12)
        assert left <= rest_left
