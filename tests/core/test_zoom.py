"""Tests for the parabolic zoom layout engine.

The zoom module computes per-icon positions using a parabolic displacement
formula derived from Plank's PositionManager.  Each icon is displaced from
its REST position based on distance from the cursor, then scaled using a
parabolic curve.

The scaling unit for h_padding and item_padding is pixels (already scaled
from the theme's "tenths of one percent of icon_size" at load time).
"""

from unittest.mock import MagicMock

import pytest

from docking.core.zoom import (
    OFFSET_PCT_SNAP,
    compute_layout,
    content_bounds,
)


class TestRestPositions:
    """When cursor_x < 0 (no hover), icons should be at rest scale (1.0x)."""

    def test_all_scales_are_1(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(5)]
        # When
        layout = compute_layout(items, config, -1.0)
        # Then
        for li in layout:
            assert li.scale == pytest.approx(1.0)

    def test_positions_are_evenly_spaced(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(5)]
        # When
        layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # Then
        for i in range(1, len(layout)):
            gap = layout[i].x - layout[i - 1].x
            assert gap == pytest.approx(48 + 10)  # icon_size + item_padding

    def test_first_icon_starts_at_h_padding(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = False
        config.zoom_percent = 1.0
        config.zoom_range = 3
        items = [MagicMock() for _ in range(3)]
        # When
        layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # Then
        assert layout[0].x == pytest.approx(12.0)


class TestZoomedPositions:
    """When cursor hovers, nearby icons should zoom up."""

    def test_hovered_icon_has_max_scale(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When — hover directly over the center icon
        center_x = rest[2].x + 24  # center of 3rd icon
        layout = compute_layout(items, config, center_x, item_padding=10, h_padding=12)
        # Then
        assert layout[2].scale == pytest.approx(config.zoom_percent)

    def test_distant_icons_stay_at_rest(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(10)]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When — hover near the left end
        layout = compute_layout(
            items, config, rest[0].x + 24, item_padding=10, h_padding=12
        )
        # Then — far-right icons should be at rest
        assert layout[-1].scale == pytest.approx(1.0)

    def test_zoom_disabled_returns_all_scale_1(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = False
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When
        layout = compute_layout(
            items, config, rest[2].x + 24, item_padding=10, h_padding=12
        )
        # Then
        for li in layout:
            assert li.scale == pytest.approx(1.0)

    def test_single_item_zooms_when_hovered(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock()]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When
        layout = compute_layout(
            items, config, rest[0].x + 24, item_padding=10, h_padding=12
        )
        # Then
        assert layout[0].scale == pytest.approx(1.5)


class TestEdgeCases:
    def test_empty_items_returns_empty(self):
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        assert compute_layout([], config, 100.0) == []

    def test_cursor_far_right_all_at_rest(self):
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(3)]
        layout = compute_layout(items, config, 99999.0, item_padding=10, h_padding=12)
        for li in layout:
            assert li.scale == pytest.approx(1.0)

    def test_offset_pct_snap_constant_near_one(self):
        assert 0.9 < OFFSET_PCT_SNAP < 1.0

    def test_cursor_exactly_between_two_icons(self):
        """Cursor between two icons -- both should zoom symmetrically."""
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 2.0
        config.zoom_range = 3
        items = [MagicMock() for _ in range(2)]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When — cursor exactly between the two icon centers
        center_x = (rest[0].x + rest[1].x + 48) / 2
        layout = compute_layout(items, config, center_x, item_padding=10, h_padding=12)
        # Then — both should have equal scale
        assert layout[0].scale == pytest.approx(layout[1].scale, abs=0.01)

    def test_hover_over_first_icon_zooms_first(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When -- hover over first icon center
        layout = compute_layout(
            items, config, rest[0].x + 24, item_padding=10, h_padding=12
        )
        # Then -- first icon zoomed, last icon at rest
        assert layout[0].scale == pytest.approx(1.5)
        assert layout[-1].scale == pytest.approx(1.0)


class TestContentBounds:
    def test_no_layout_returns_zero_and_padding(self):
        # Given / When -- with item_padding, total pad = h_padding + item_padding/2
        left, right = content_bounds([], 48, 12, item_padding=10)
        # Then
        assert left == 0.0
        assert right == pytest.approx(2 * (12 + 5))  # 34.0

    def test_no_layout_without_item_padding(self):
        left, right = content_bounds([], 48, 12)
        assert right == pytest.approx(24.0)

    def test_rest_layout_includes_half_item_padding_per_side(self):
        """Shelf extends item_padding/2 beyond first/last icon edges."""
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = False
        config.zoom_percent = 1.0
        config.zoom_range = 3
        items = [MagicMock() for _ in range(3)]
        layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When
        left, right = content_bounds(layout, 48, 12, item_padding=10)
        # Then -- width = 2*(h_pad + item_pad/2) + 3*48 + 2*10
        expected_w = (12 + 5) * 2 + 3 * 48 + 2 * 10
        assert right - left == pytest.approx(expected_w)

    def test_half_item_padding_extends_beyond_icon_edges(self):
        """content_bounds with item_padding should be wider than without."""
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = False
        config.zoom_percent = 1.0
        config.zoom_range = 3
        items = [MagicMock() for _ in range(3)]
        layout = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        # When
        with_ipad = content_bounds(layout, 48, 12, item_padding=10)
        without_ipad = content_bounds(layout, 48, 12, item_padding=0)
        # Then -- with item_padding adds 10px total (5 per side)
        diff = (with_ipad[1] - with_ipad[0]) - (without_ipad[1] - without_ipad[0])
        assert diff == pytest.approx(10.0)

    def test_zoomed_bounds_wider_than_rest(self):
        # Given
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=10, h_padding=12)
        rest_l, rest_r = content_bounds(rest, 48, 12, item_padding=10)
        # When
        zoomed = compute_layout(items, config, 150.0, item_padding=10, h_padding=12)
        zoom_l, zoom_r = content_bounds(zoomed, 48, 12, item_padding=10)
        # Then
        assert (zoom_r - zoom_l) >= (rest_r - rest_l)

    def test_left_edge_can_go_negative_during_zoom(self):
        """When hovering right side, left icons displace left."""
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
        left, _ = content_bounds(zoomed, 48, 12, item_padding=10)
        # Then
        rest_left, _ = content_bounds(rest, 48, 12, item_padding=10)
        assert left <= rest_left


class TestBaseWConsistency:
    """base_w used for cursor conversion must match content_bounds at rest.

    If these diverge, cursor-to-content-space conversion produces wrong
    values, causing zoom to center on the wrong position. This is an
    integration test guarding the contract between renderer/dock_window
    and zoom.py.
    """

    def test_base_w_matches_rest_content_width(self):
        # Given -- same parameters used in renderer.draw()
        h_padding = 2.0
        item_padding = 12.0
        icon_size = 48
        n = 5
        config = MagicMock()
        config.icon_size = icon_size
        config.zoom_enabled = False
        config.zoom_percent = 1.0
        config.zoom_range = 3
        items = [MagicMock() for _ in range(n)]
        # When
        pad = h_padding + item_padding / 2
        base_w = pad * 2 + n * icon_size + max(0, n - 1) * item_padding
        layout = compute_layout(
            items, config, -1.0, item_padding=item_padding, h_padding=h_padding
        )
        left, right = content_bounds(
            layout=layout,
            icon_size=icon_size,
            h_padding=h_padding,
            item_padding=item_padding,
        )
        bounds_w = right - left
        # Then -- must match exactly
        assert base_w == pytest.approx(bounds_w)

    def test_consistency_across_item_counts(self):
        for n in (1, 2, 5, 11):
            h_pad, i_pad, size = 2.0, 12.0, 48
            config = MagicMock()
            config.icon_size = size
            config.zoom_enabled = False
            config.zoom_percent = 1.0
            config.zoom_range = 3
            items = [MagicMock() for _ in range(n)]
            pad = h_pad + i_pad / 2
            base_w = pad * 2 + n * size + max(0, n - 1) * i_pad
            layout = compute_layout(
                items, config, -1.0, item_padding=i_pad, h_padding=h_pad
            )
            left, right = content_bounds(
                layout=layout, icon_size=size, h_padding=h_pad, item_padding=i_pad
            )
            assert base_w == pytest.approx(right - left), f"mismatch at n={n}"

    def test_consistency_with_default_theme_values(self):
        """Use actual default theme values to catch real-world mismatches."""
        from docking.core.theme import Theme

        theme = Theme.load("default", 48)
        n = 8
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = False
        config.zoom_percent = 1.0
        config.zoom_range = 3
        items = [MagicMock() for _ in range(n)]
        pad = theme.h_padding + theme.item_padding / 2
        base_w = pad * 2 + n * 48 + max(0, n - 1) * theme.item_padding
        layout = compute_layout(
            items,
            config,
            -1.0,
            item_padding=theme.item_padding,
            h_padding=theme.h_padding,
        )
        left, right = content_bounds(
            layout=layout,
            icon_size=48,
            h_padding=theme.h_padding,
            item_padding=theme.item_padding,
        )
        assert base_w == pytest.approx(right - left)


class TestZoomProgressDecay:
    """zoom_progress parameter controls zoom decay during hide animation.

    Matches Plank's zoom_in_percent = 1.0 + (ZoomPercent - 1.0) * zoom_in_progress.
    Both scale AND displacement must decay together so icons compress toward
    their rest centers during hide.
    """

    def _make_config(self):
        config = MagicMock()
        config.icon_size = 48
        config.zoom_enabled = True
        config.zoom_percent = 1.5
        config.zoom_range = 3
        return config

    def test_zoom_progress_1_gives_full_zoom(self):
        # Given -- cursor over center icon
        config = self._make_config()
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=12, h_padding=2)
        cursor = rest[2].x + 24
        # When
        full = compute_layout(
            items, config, cursor, item_padding=12, h_padding=2, zoom_progress=1.0
        )
        # Then -- center icon at max zoom
        assert full[2].scale == pytest.approx(1.5)

    def test_zoom_progress_0_gives_rest(self):
        # Given
        config = self._make_config()
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=12, h_padding=2)
        cursor = rest[2].x + 24
        # When -- fully hidden
        decayed = compute_layout(
            items, config, cursor, item_padding=12, h_padding=2, zoom_progress=0.0
        )
        # Then -- all at rest scale
        for li in decayed:
            assert li.scale == pytest.approx(1.0)

    def test_displacement_collapses_with_zoom_progress(self):
        """Icons must compress toward rest centers as zoom decays."""
        # Given
        config = self._make_config()
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=12, h_padding=2)
        cursor = rest[2].x + 24
        full = compute_layout(
            items, config, cursor, item_padding=12, h_padding=2, zoom_progress=1.0
        )
        # When
        half = compute_layout(
            items, config, cursor, item_padding=12, h_padding=2, zoom_progress=0.5
        )
        decayed = compute_layout(
            items, config, cursor, item_padding=12, h_padding=2, zoom_progress=0.0
        )
        # Then -- spread shrinks: full > half > decayed (== rest)
        full_spread = full[-1].x - full[0].x
        half_spread = half[-1].x - half[0].x
        rest_spread = rest[-1].x - rest[0].x
        decayed_spread = decayed[-1].x - decayed[0].x
        assert full_spread > half_spread
        assert half_spread > rest_spread or half_spread == pytest.approx(rest_spread)
        assert decayed_spread == pytest.approx(rest_spread)

    def test_content_bounds_shrink_with_zoom_progress(self):
        """Shelf width should track zoom decay -- no growing edge gaps."""
        config = self._make_config()
        items = [MagicMock() for _ in range(5)]
        rest = compute_layout(items, config, -1.0, item_padding=12, h_padding=2)
        cursor = rest[2].x + 24
        full = compute_layout(
            items, config, cursor, item_padding=12, h_padding=2, zoom_progress=1.0
        )
        decayed = compute_layout(
            items, config, cursor, item_padding=12, h_padding=2, zoom_progress=0.0
        )
        # When
        fl, fr = content_bounds(layout=full, icon_size=48, h_padding=2, item_padding=12)
        dl, dr = content_bounds(
            layout=decayed, icon_size=48, h_padding=2, item_padding=12
        )
        rl, rr = content_bounds(layout=rest, icon_size=48, h_padding=2, item_padding=12)
        # Then
        assert (fr - fl) > (dr - dl)
        assert (dr - dl) == pytest.approx(rr - rl)
