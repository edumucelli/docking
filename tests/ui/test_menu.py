"""Tests for menu constants and configuration."""

import sys
from unittest.mock import MagicMock

gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.core.position import Position  # noqa: E402
from docking.core.theme import _BUILTIN_THEMES_DIR  # noqa: E402
from docking.ui.menu import ICON_SIZE_OPTIONS, _build_radio_submenu  # noqa: E402


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


class TestBuildRadioSubmenu:
    def test_returns_menu_item_with_submenu(self):
        callback = MagicMock()
        item = _build_radio_submenu(
            label="Test", items=[("A", 1), ("B", 2)], current=1, on_changed=callback
        )
        assert item.get_label() == "Test"
        assert item.get_submenu() is not None

    def test_correct_number_of_children(self):
        item = _build_radio_submenu(
            label="Test",
            items=[("A", 1), ("B", 2), ("C", 3)],
            current=1,
            on_changed=MagicMock(),
        )
        children = item.get_submenu().get_children()
        assert len(children) == 3

    def test_active_item_is_set(self):
        item = _build_radio_submenu(
            label="Test", items=[("A", 1), ("B", 2)], current=2, on_changed=MagicMock()
        )
        children = item.get_submenu().get_children()
        # Second item (value=2) should be active
        assert children[1].get_active()
