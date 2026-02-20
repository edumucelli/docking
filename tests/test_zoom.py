"""Tests for parabolic zoom math."""

import pytest
from unittest.mock import MagicMock

from docking.zoom import compute_icon_zoom, compute_layout, total_width, LayoutItem


class TestComputeIconZoom:
    def test_cursor_off_dock_returns_no_zoom(self):
        assert compute_icon_zoom(-1.0, 100.0, 48, 2.0, 3) == 1.0

    def test_cursor_directly_on_icon_returns_max_zoom(self):
        result = compute_icon_zoom(100.0, 100.0, 48, 2.0, 3)
        assert result == pytest.approx(2.0)

    def test_cursor_at_max_range_returns_no_zoom(self):
        icon_size, zoom_range = 48, 3
        max_dist = icon_size * zoom_range  # 144
        result = compute_icon_zoom(100.0 + max_dist, 100.0, icon_size, 2.0, zoom_range)
        assert result == pytest.approx(1.0)

    def test_cursor_beyond_max_range_clamps(self):
        result = compute_icon_zoom(500.0, 100.0, 48, 2.0, 3)
        assert result == pytest.approx(1.0)

    def test_half_distance_gives_intermediate_zoom(self):
        icon_size, zoom_range = 48, 3
        half_dist = (icon_size * zoom_range) / 2
        result = compute_icon_zoom(100.0 + half_dist, 100.0, icon_size, 2.0, zoom_range)
        # At half distance: offset_pct=0.5, zoom=1-0.25=0.75, scale=1+0.75*1=1.75
        assert result == pytest.approx(1.75)

    def test_zoom_percent_of_1_always_returns_1(self):
        result = compute_icon_zoom(100.0, 100.0, 48, 1.0, 3)
        assert result == pytest.approx(1.0)

    def test_symmetry(self):
        """Zoom should be same distance left or right of center."""
        left = compute_icon_zoom(80.0, 100.0, 48, 2.0, 3)
        right = compute_icon_zoom(120.0, 100.0, 48, 2.0, 3)
        assert left == pytest.approx(right)


class TestComputeLayout:
    def _make_items(self, n: int) -> list:
        return [MagicMock() for _ in range(n)]

    def _make_config(self, icon_size=48, zoom_enabled=True, zoom_percent=2.0, zoom_range=3):
        config = MagicMock()
        config.icon_size = icon_size
        config.zoom_enabled = zoom_enabled
        config.zoom_percent = zoom_percent
        config.zoom_range = zoom_range
        return config

    def test_empty_items_returns_empty(self):
        config = self._make_config()
        assert compute_layout([], config, 100.0) == []

    def test_no_zoom_uniform_scales(self):
        items = self._make_items(3)
        config = self._make_config(zoom_enabled=False)
        layout = compute_layout(items, config, 100.0, item_padding=6, h_padding=12)
        for li in layout:
            assert li.scale == pytest.approx(1.0)

    def test_cursor_off_dock_no_zoom(self):
        items = self._make_items(3)
        config = self._make_config()
        layout = compute_layout(items, config, -1.0, item_padding=6, h_padding=12)
        for li in layout:
            assert li.scale == pytest.approx(1.0)

    def test_layout_x_positions_monotonically_increase(self):
        items = self._make_items(5)
        config = self._make_config()
        layout = compute_layout(items, config, 100.0, item_padding=6, h_padding=12)
        for i in range(1, len(layout)):
            assert layout[i].x > layout[i - 1].x

    def test_zoomed_layout_wider_than_unzoomed(self):
        items = self._make_items(5)
        config = self._make_config()
        layout_zoomed = compute_layout(items, config, 100.0, item_padding=6, h_padding=12)
        layout_flat = compute_layout(items, config, -1.0, item_padding=6, h_padding=12)
        w_zoomed = total_width(layout_zoomed, 48, 6, 12)
        w_flat = total_width(layout_flat, 48, 6, 12)
        assert w_zoomed >= w_flat

    def test_single_item(self):
        items = self._make_items(1)
        config = self._make_config()
        layout = compute_layout(items, config, -1.0, item_padding=6, h_padding=12)
        assert len(layout) == 1
        assert layout[0].x == pytest.approx(12.0)
        assert layout[0].scale == pytest.approx(1.0)


class TestTotalWidth:
    def test_empty_layout(self):
        assert total_width([], 48, 6, 12) == pytest.approx(24.0)

    def test_single_item_no_zoom(self):
        layout = [LayoutItem(x=12.0, scale=1.0)]
        # 12 + 48*1 + 12 = 72
        assert total_width(layout, 48, 6, 12) == pytest.approx(72.0)

    def test_width_increases_with_zoom(self):
        flat = [LayoutItem(x=12.0, scale=1.0), LayoutItem(x=66.0, scale=1.0)]
        zoomed = [LayoutItem(x=12.0, scale=1.5), LayoutItem(x=84.0, scale=1.5)]
        assert total_width(zoomed, 48, 6, 12) > total_width(flat, 48, 6, 12)


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
        items = [MagicMock() for _ in range(5)]
        config = self._make_config(zoom_enabled=False)
        layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        w = total_width(layout, 48, 10, 12)
        expected = 12 + 5 * 48 + 4 * 10 + 12  # h_pad + icons + gaps + h_pad
        assert w == pytest.approx(expected)

    def test_zoomed_width_larger_than_base(self):
        items = [MagicMock() for _ in range(5)]
        config = self._make_config()
        base_layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        base_w = total_width(base_layout, 48, 10, 12)

        zoomed_layout = compute_layout(items, config, 150.0, item_padding=10, h_padding=12)
        zoomed_w = total_width(zoomed_layout, 48, 10, 12)

        assert zoomed_w > base_w

    def test_center_hover_is_widest(self):
        """Hovering at center produces the widest layout."""
        items = [MagicMock() for _ in range(5)]
        config = self._make_config()
        # Base width to find center
        base_layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        base_w = total_width(base_layout, 48, 10, 12)
        center = base_w / 2

        center_layout = compute_layout(items, config, center, item_padding=10, h_padding=12)
        center_w = total_width(center_layout, 48, 10, 12)

        edge_layout = compute_layout(items, config, 12.0, item_padding=10, h_padding=12)
        edge_w = total_width(edge_layout, 48, 10, 12)

        assert center_w >= edge_w
