"""Tests for theme loading and color parsing."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from docking.core.theme import Theme, _rgba


class TestRgba:
    def test_white_opaque(self):
        # Given / When
        result = _rgba([255, 255, 255, 255])
        # Then
        assert result == pytest.approx((1.0, 1.0, 1.0, 1.0))

    def test_black_transparent(self):
        # Given / When
        result = _rgba([0, 0, 0, 0])
        # Then
        assert result == pytest.approx((0.0, 0.0, 0.0, 0.0))

    def test_mid_values(self):
        # Given / When
        r, g, b, a = _rgba([128, 64, 32, 200])
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
        t = Theme.load("default")
        # Then
        assert t.roundness == 5.0
        assert t.stroke_width == 1.0

    def test_load_missing_theme_returns_defaults(self):
        # Given / When
        t = Theme.load("nonexistent-theme-name")
        # Then
        assert t == Theme()

    def test_load_partial_theme(self, tmp_path):
        """Theme file with only some keys — rest use defaults."""
        # Given
        theme_data = {"roundness": 16, "stroke_width": 2.0}
        theme_file = tmp_path / "custom.json"
        theme_file.write_text(json.dumps(theme_data))
        # When
        with patch("docking.core.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("custom")
        # Then
        assert t.roundness == 16.0
        assert t.stroke_width == 2.0
        # Defaults for unspecified
        assert t.indicator_radius == 2.5
