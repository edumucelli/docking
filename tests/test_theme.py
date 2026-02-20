"""Tests for theme loading and color parsing."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from docking.theme import Theme, _rgba


class TestRgba:
    def test_white_opaque(self):
        assert _rgba([255, 255, 255, 255]) == pytest.approx((1.0, 1.0, 1.0, 1.0))

    def test_black_transparent(self):
        assert _rgba([0, 0, 0, 0]) == pytest.approx((0.0, 0.0, 0.0, 0.0))

    def test_mid_values(self):
        r, g, b, a = _rgba([128, 64, 32, 200])
        assert r == pytest.approx(128 / 255)
        assert g == pytest.approx(64 / 255)
        assert b == pytest.approx(32 / 255)
        assert a == pytest.approx(200 / 255)


class TestThemeDefaults:
    def test_default_theme_has_valid_colors(self):
        t = Theme()
        assert len(t.fill_start) == 4
        assert all(0 <= c <= 1 for c in t.fill_start)
        assert t.roundness > 0
        assert t.indicator_radius > 0


class TestThemeLoad:
    def test_load_default_theme(self):
        t = Theme.load("default")
        assert t.roundness == 8.0
        assert t.stroke_width == 1.0

    def test_load_missing_theme_returns_defaults(self):
        t = Theme.load("nonexistent-theme-name")
        assert t == Theme()

    def test_load_partial_theme(self, tmp_path):
        """Theme file with only some keys — rest use defaults."""
        theme_data = {"roundness": 16, "stroke_width": 2.0}
        theme_file = tmp_path / "custom.json"
        theme_file.write_text(json.dumps(theme_data))

        with patch("docking.theme._BUILTIN_THEMES_DIR", tmp_path):
            t = Theme.load("custom")
        assert t.roundness == 16.0
        assert t.stroke_width == 2.0
        # Defaults for unspecified
        assert t.indicator_radius == 2.5
