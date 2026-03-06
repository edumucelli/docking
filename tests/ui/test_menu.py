"""Tests for menu constants and configuration."""

from unittest.mock import MagicMock

import docking.ui.menu as menu_mod
from docking.core.position import Position
from docking.core.theme import _BUILTIN_THEMES_DIR
from docking.ui.menu import ICON_SIZE_OPTIONS, _build_radio_submenu


class _FakeMenu:
    def __init__(self) -> None:
        self.children = []

    def append(self, item) -> None:
        self.children.append(item)

    def get_children(self):
        return list(self.children)


class _FakeMenuItem:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._submenu = None

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu

    def get_label(self) -> str:
        return self._label


class _FakeRadioMenuItem(_FakeMenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label=label)
        self._active = False

    def join_group(self, _other) -> None:
        return

    def set_active(self, active: bool) -> None:
        self._active = active

    def get_active(self) -> bool:
        return self._active

    def connect(self, *_args, **_kwargs) -> None:
        return


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

    def test_new_builtin_themes_exist(self):
        assert (_BUILTIN_THEMES_DIR / "nord.json").exists()
        assert (_BUILTIN_THEMES_DIR / "gruvbox.json").exists()
        assert (_BUILTIN_THEMES_DIR / "solarized.json").exists()


class TestBuildRadioSubmenu:
    def test_returns_menu_item_with_submenu(self, monkeypatch):
        monkeypatch.setattr(
            menu_mod,
            "Gtk",
            type(
                "FakeGtk",
                (),
                {
                    "Menu": _FakeMenu,
                    "MenuItem": _FakeMenuItem,
                    "RadioMenuItem": _FakeRadioMenuItem,
                },
            ),
        )
        callback = MagicMock()
        item = _build_radio_submenu(
            label="Test", items=[("A", 1), ("B", 2)], current=1, on_changed=callback
        )
        assert item.get_label() == "Test"
        assert item.get_submenu() is not None

    def test_correct_number_of_children(self, monkeypatch):
        monkeypatch.setattr(
            menu_mod,
            "Gtk",
            type(
                "FakeGtk",
                (),
                {
                    "Menu": _FakeMenu,
                    "MenuItem": _FakeMenuItem,
                    "RadioMenuItem": _FakeRadioMenuItem,
                },
            ),
        )
        item = _build_radio_submenu(
            label="Test",
            items=[("A", 1), ("B", 2), ("C", 3)],
            current=1,
            on_changed=MagicMock(),
        )
        children = item.get_submenu().get_children()
        assert len(children) == 3

    def test_active_item_is_set(self, monkeypatch):
        monkeypatch.setattr(
            menu_mod,
            "Gtk",
            type(
                "FakeGtk",
                (),
                {
                    "Menu": _FakeMenu,
                    "MenuItem": _FakeMenuItem,
                    "RadioMenuItem": _FakeRadioMenuItem,
                },
            ),
        )
        item = _build_radio_submenu(
            label="Test", items=[("A", 1), ("B", 2)], current=2, on_changed=MagicMock()
        )
        children = item.get_submenu().get_children()
        # Second item (value=2) should be active
        assert children[1].get_active()
