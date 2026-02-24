"""Tests for menu constants and configuration."""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.ui.menu import ICON_SIZE_OPTIONS  # noqa: E402
from docking.core.position import Position  # noqa: E402
from docking.core.theme import _BUILTIN_THEMES_DIR  # noqa: E402


class TestIconSizeOptions:
    def test_has_multiple_sizes(self):
        assert len(ICON_SIZE_OPTIONS) >= 3

    def test_sorted_ascending(self):
        assert list(ICON_SIZE_OPTIONS) == sorted(ICON_SIZE_OPTIONS)

    def test_all_positive(self):
        assert all(s > 0 for s in ICON_SIZE_OPTIONS)

    def test_default_48_included(self):
        assert 48 in ICON_SIZE_OPTIONS


class TestPositionMenuEntries:
    """Position submenu should cover all Position enum values."""

    def test_all_positions_have_capitalizable_labels(self):
        for pos in Position:
            label = pos.value.capitalize()
            assert label and label[0].isupper()


class TestThemeDiscovery:
    def test_builtin_themes_dir_exists(self):
        assert _BUILTIN_THEMES_DIR.is_dir()

    def test_at_least_one_theme_json(self):
        themes = list(_BUILTIN_THEMES_DIR.glob("*.json"))
        assert len(themes) >= 1

    def test_default_theme_exists(self):
        assert (_BUILTIN_THEMES_DIR / "default.json").exists()
