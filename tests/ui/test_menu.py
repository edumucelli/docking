"""Tests for menu constants and configuration."""

from unittest.mock import MagicMock

import docking.ui.menu as menu_mod
from docking.core.position import Position
from docking.core.theme import _BUILTIN_THEMES_DIR
from docking.ui.menu import _build_radio_submenu


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
        self._child = None

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu

    def get_label(self) -> str:
        return self._label

    def set_label(self, label: str) -> None:
        self._label = label

    def get_child(self):
        return self._child

    def remove(self, _child) -> None:
        self._child = None

    def add(self, child) -> None:
        self._child = child

    def set_sensitive(self, sensitive: bool) -> None:
        self._sensitive = sensitive


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


class _FakeBox:
    def __init__(self, **_kwargs) -> None:
        self.children = []

    def pack_start(self, child, *_args) -> None:
        self.children.append(child)


class _FakeLabel:
    def __init__(self, label: str = "") -> None:
        self.label = label
        self.xalign = None
        self.max_width_chars = None
        self.ellipsize = None
        self.single_line_mode = False

    def set_xalign(self, value: float) -> None:
        self.xalign = value

    def set_max_width_chars(self, value: int) -> None:
        self.max_width_chars = value

    def set_ellipsize(self, value) -> None:
        self.ellipsize = value

    def set_single_line_mode(self, value: bool) -> None:
        self.single_line_mode = value


class _FakeImage:
    def __init__(self) -> None:
        self.pixel_size = None

    @classmethod
    def new_from_pixbuf(cls, pixbuf):
        image = cls()
        image.pixbuf = pixbuf
        return image

    def set_pixel_size(self, value: int) -> None:
        self.pixel_size = value


class _FakePixbuf:
    def __init__(self, width: int, height: int, scaled=None) -> None:
        self.width = width
        self.height = height
        self.scaled = scaled
        self.scale_calls: list[tuple[int, int, object]] = []

    def get_width(self) -> int:
        return self.width

    def get_height(self) -> int:
        return self.height

    def scale_simple(self, width: int, height: int, interp):
        self.scale_calls.append((width, height, interp))
        return self.scaled


class _FakeGtk:
    Menu = _FakeMenu
    MenuItem = _FakeMenuItem
    CheckMenuItem = _FakeMenuItem
    RadioMenuItem = _FakeRadioMenuItem
    Box = _FakeBox
    Label = _FakeLabel
    Image = _FakeImage
    Orientation = type("Orientation", (), {"HORIZONTAL": 0})


class _FakePango:
    EllipsizeMode = type("EllipsizeMode", (), {"END": 1})


class _FakeGdkPixbuf:
    InterpType = type("InterpType", (), {"BILINEAR": 1})


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
        assert (_BUILTIN_THEMES_DIR / "paper.json").exists()
        assert (_BUILTIN_THEMES_DIR / "candy.json").exists()
        assert (_BUILTIN_THEMES_DIR / "pill.json").exists()


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


class TestMenuIcons:
    def test_set_menu_item_icon_scales_pixbuf_and_replaces_child(self, monkeypatch):
        monkeypatch.setattr(menu_mod, "Gtk", _FakeGtk)
        monkeypatch.setattr(menu_mod, "Pango", _FakePango)
        monkeypatch.setattr(menu_mod, "GdkPixbuf", _FakeGdkPixbuf)
        item = _FakeMenuItem()
        item.add(object())
        scaled = object()
        pixbuf = _FakePixbuf(48, 48, scaled=scaled)

        menu_mod._set_menu_item_icon(
            item=item,
            label="Folder",
            pixbuf=pixbuf,
            icon_px=24,
        )

        row = item.get_child()
        assert item.get_label() == "Folder"
        assert pixbuf.scale_calls == [(24, 24, _FakeGdkPixbuf.InterpType.BILINEAR)]
        assert len(row.children) == 2
        assert row.children[0].pixbuf is scaled
        assert row.children[0].pixel_size == 24

    def test_set_menu_item_icon_no_existing_child(self, monkeypatch):
        """Should still work when there is no child to remove."""
        monkeypatch.setattr(menu_mod, "Gtk", _FakeGtk)
        monkeypatch.setattr(menu_mod, "Pango", _FakePango)
        monkeypatch.setattr(menu_mod, "GdkPixbuf", _FakeGdkPixbuf)
        item = _FakeMenuItem()
        # No child added
        scaled = object()
        pixbuf = _FakePixbuf(32, 32, scaled=scaled)

        menu_mod._set_menu_item_icon(
            item=item,
            label="App",
            pixbuf=pixbuf,
            icon_px=16,
        )

        assert item.get_label() == "App"
        row = item.get_child()
        assert len(row.children) == 2


class TestMakeMenuHeader:
    def test_make_menu_header_creates_label(self, monkeypatch):
        monkeypatch.setattr(menu_mod, "Gtk", _FakeGtk)
        monkeypatch.setattr(menu_mod, "Pango", _FakePango)
        item = menu_mod._make_menu_header("Test Header")
        assert isinstance(item, _FakeMenuItem)
        assert item.get_label() == "Test Header"
