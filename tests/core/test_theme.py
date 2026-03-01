"""Tests for theme loading, scaling unit system, and color parsing."""

import json
from unittest.mock import patch

import pytest

from docking.core.theme import Theme, _rgba


class TestRgba:
    def test_white_opaque(self):
        # Given / When
        result = _rgba(values=[255, 255, 255, 255])
        # Then
        assert result == pytest.approx((1.0, 1.0, 1.0, 1.0))

    def test_black_transparent(self):
        # Given / When
        result = _rgba(values=[0, 0, 0, 0])
        # Then
        assert result == pytest.approx((0.0, 0.0, 0.0, 0.0))

    def test_mid_values(self):
        # Given / When
        r, g, b, a = _rgba(values=[128, 64, 32, 200])
        # Then
        assert r == pytest.approx(128 / 255)
        assert g == pytest.approx(64 / 255)
        assert b == pytest.approx(32 / 255)
        assert a == pytest.approx(200 / 255)


class TestThemeDefaults:
    def test_default_theme_has_valid_colors(self):
        # Given / When
        t = Theme()
        # Then
        assert len(t.fill_start) == 4
        assert all(0 <= c <= 1 for c in t.fill_start)
        assert t.roundness > 0
        assert t.indicator_radius > 0


class TestThemeLoad:
    def test_load_default_theme(self):
        # Given / When
        t = Theme.load("default", 48)
        # Then
        assert t.roundness == 5.0
        assert t.stroke_width == 1.0

    def test_load_missing_theme_returns_defaults(self):
        # Given / When
        t = Theme.load("nonexistent-theme-name", 48)
        # Then
        assert t == Theme()

    def test_load_partial_theme(self, tmp_path):
        """Theme file with only some keys -- rest use defaults."""
        # Given
        theme_data = {"roundness": 16, "stroke_width": 2.0}
        theme_file = tmp_path / "custom.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("custom", 48)
        # Then
        assert t.roundness == 16.0
        assert t.stroke_width == 2.0
        # Defaults for unspecified
        assert t.indicator_radius == 2.5


class TestScalingUnit:
    """Tests for the scaling unit system: JSON values * (icon_size / 10)."""

    def test_default_48px_item_padding(self):
        # Given
        # scale = 48/10 = 4.8, so 2.5 * 4.8 = 12.0
        t = Theme.load("default", 48)
        # Then
        assert t.item_padding == pytest.approx(12.0)

    def test_default_48px_top_padding(self):
        # Given
        # scale = 4.8, so -7 * 4.8 = -33.6
        t = Theme.load("default", 48)
        # Then
        assert t.top_padding == pytest.approx(-33.6)

    def test_default_48px_bottom_padding(self):
        # Given
        # scale = 4.8, so 1 * 4.8 = 4.8
        t = Theme.load("default", 48)
        # Then
        assert t.bottom_padding == pytest.approx(4.8)

    def test_default_64px_scales_proportionally(self):
        # Given
        t48 = Theme.load("default", 48)
        t64 = Theme.load("default", 64)
        # Then
        ratio = 64 / 48
        assert t64.item_padding == pytest.approx(t48.item_padding * ratio, rel=1e-6)
        assert t64.top_padding == pytest.approx(t48.top_padding * ratio, rel=1e-6)
        assert t64.bottom_padding == pytest.approx(t48.bottom_padding * ratio, rel=1e-6)

    def test_h_padding_fallback_when_zero(self):
        # Given
        # When h_padding <= 0, fallback = 2 * stroke_width = 2.0
        t = Theme.load("default", 48)
        # Then
        assert t.h_padding == pytest.approx(2.0)

    def test_h_padding_positive_uses_scaled(self, tmp_path):
        # Given
        # 3 * 4.8 = 14.4 > 0, so no fallback
        theme_data = {"h_padding": 3, "stroke_width": 1.0}
        theme_file = tmp_path / "pos.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("pos", 48)
        # Then
        assert t.h_padding == pytest.approx(14.4)

    def test_indicator_radius_from_size(self):
        # Given
        # indicator_radius = 5 / 2 = 2.5 (NOT scaled)
        t = Theme.load("default", 48)
        # Then
        assert t.indicator_radius == pytest.approx(2.5)


class TestShelfHeightDerivation:
    """shelf_height = max(0, icon_size + top_offset + bottom_offset)

    top_offset = 2 * stroke_width + top_padding_px
    bottom_offset = bottom_padding_px
    """

    def test_default_48px_shelf_height(self):
        # Given
        # top_padding_px = -7 * 4.8 = -33.6
        # bottom_padding_px = 1 * 4.8 = 4.8
        # top_offset = 2 * 1.0 + (-33.6) = -31.6
        # bottom_offset = 4.8
        # shelf_height = max(0, 48 + (-31.6) + 4.8) = 21.2
        t = Theme.load("default", 48)
        # Then
        assert t.shelf_height == pytest.approx(21.2)

    def test_default_64px_shelf_height(self):
        # Given
        # scale = 6.4
        # top_padding_px = -7 * 6.4 = -44.8
        # bottom_padding_px = 1 * 6.4 = 6.4
        # top_offset = 2 * 1.0 + (-44.8) = -42.8
        # bottom_offset = 6.4
        # shelf_height = max(0, 64 + (-42.8) + 6.4) = 27.6
        t = Theme.load("default", 64)
        # Then
        assert t.shelf_height == pytest.approx(27.6)

    def test_shelf_height_never_negative(self, tmp_path):
        # Given
        theme_data = {"top_padding": -20, "bottom_padding": -5}
        theme_file = tmp_path / "neg.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("neg", 48)
        # Then
        assert t.shelf_height >= 0.0


class TestAnimationParams:
    """Animation params are loaded directly, NOT scaled."""

    def test_default_bounce_heights(self):
        t = Theme.load("default", 48)
        assert t.urgent_bounce_height == pytest.approx(1.66)
        assert t.launch_bounce_height == pytest.approx(0.625)

    def test_default_durations(self):
        t = Theme.load("default", 48)
        assert t.urgent_bounce_time_ms == 600
        assert t.launch_bounce_time_ms == 600
        assert t.click_time_ms == 300
        assert t.active_time_ms == 150

    def test_default_visual_params(self):
        t = Theme.load("default", 48)
        assert t.hover_lighten == pytest.approx(0.2)
        assert t.max_indicator_dots == 3
        assert t.glow_opacity == pytest.approx(0.6)

    def test_animation_params_same_at_different_icon_sizes(self):
        # Given
        t48 = Theme.load("default", 48)
        t64 = Theme.load("default", 64)
        # Then
        assert t48.urgent_bounce_height == t64.urgent_bounce_height
        assert t48.launch_bounce_time_ms == t64.launch_bounce_time_ms
        assert t48.hover_lighten == t64.hover_lighten
        assert t48.glow_opacity == t64.glow_opacity

    def test_missing_animation_keys_use_defaults(self, tmp_path):
        # Given
        theme_data = {"roundness": 5}
        theme_file = tmp_path / "minimal.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("minimal", 48)
        # Then
        assert t.urgent_bounce_height == pytest.approx(1.66)
        assert t.launch_bounce_height == pytest.approx(0.625)
        assert t.click_time_ms == 300
        assert t.hover_lighten == pytest.approx(0.2)
        assert t.max_indicator_dots == 3
        assert t.glow_opacity == pytest.approx(0.6)
