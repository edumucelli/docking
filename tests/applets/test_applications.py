"""Tests for the Applications applet."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docking.applets.applications.applet as applications_applet_mod
from docking.applets.applications import (
    ApplicationsApplet,
    _build_app_categories,
)


class TestBuildAppCategories:
    def test_returns_dict(self):
        categories = _build_app_categories()
        assert isinstance(categories, dict)

    def test_excludes_hidden_apps(self):
        mock_app = MagicMock()
        mock_app.get_is_hidden.return_value = True
        mock_app.get_nodisplay.return_value = False

        with patch(
            "docking.applets.applications.state.Gio.AppInfo.get_all",
            return_value=[mock_app],
        ):
            cats = _build_app_categories()
        # Hidden app should not appear in any category
        total = sum(len(apps) for apps in cats.values())
        assert total == 0


class TestApplicationsApplet:
    def _fake_gtk(self, monkeypatch):
        class _FakeMenu:
            def __init__(self) -> None:
                self.children: list[object] = []
                self.shown = False

            def append(self, item) -> None:
                self.children.append(item)
                item.parent = self

            def remove(self, item) -> None:
                self.children.remove(item)

            def get_children(self) -> list[object]:
                return list(self.children)

            def show_all(self) -> None:
                self.shown = True

        class _FakeMenuItem:
            def __init__(self, label: str = "") -> None:
                self._label = label
                self._submenu = None
                self._signals: dict[str, list[object]] = {}
                self._child = None
                self.visible = True
                self.parent = None

            def get_label(self) -> str:
                return self._label

            def connect(self, signal: str, callback) -> None:
                self._signals.setdefault(signal, []).append(callback)

            def set_submenu(self, submenu) -> None:
                self._submenu = submenu

            def get_submenu(self):
                return self._submenu

            def add(self, child) -> None:
                self._child = child

            def show(self) -> None:
                self.visible = True

            def hide(self) -> None:
                self.visible = False

        class _FakeSeparatorMenuItem(_FakeMenuItem):
            pass

        class _FakeBox:
            def __init__(self, **_kwargs) -> None:
                self.children: list[object] = []

            def pack_start(self, child, *_args) -> None:
                self.children.append(child)

        class _FakeEntry:
            def __init__(self) -> None:
                self.placeholder = ""
                self.width_chars = 0
                self.text = ""
                self.focused = False
                self._signals: dict[str, list[object]] = {}

            def set_placeholder_text(self, text: str) -> None:
                self.placeholder = text

            def set_width_chars(self, value: int) -> None:
                self.width_chars = value

            def connect(self, signal: str, callback) -> None:
                self._signals.setdefault(signal, []).append(callback)

            def get_text(self) -> str:
                return self.text

            def set_text(self, text: str) -> None:
                self.text = text

            def emit(self, signal: str) -> None:
                for callback in self._signals.get(signal, []):
                    callback(self)

            def grab_focus(self) -> None:
                self.focused = True

        monkeypatch.setattr(
            applications_applet_mod,
            "Gtk",
            SimpleNamespace(
                Menu=_FakeMenu,
                MenuItem=_FakeMenuItem,
                SeparatorMenuItem=_FakeSeparatorMenuItem,
                Box=_FakeBox,
                Entry=_FakeEntry,
                Orientation=SimpleNamespace(HORIZONTAL=0),
            ),
        )
        monkeypatch.setattr(
            applications_applet_mod,
            "make_menu_item_with_icon",
            lambda label, **_kwargs: _FakeMenuItem(label),
        )

    def test_creates_with_icon(self):
        d = ApplicationsApplet(48)
        assert d.item.icon is not None

    def test_no_click_action(self):
        d = ApplicationsApplet(48)
        # on_clicked is inherited no-op from Applet base
        d.on_clicked()  # should not crash

    def test_menu_returns_items(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        d = ApplicationsApplet(48)
        items = d.get_menu_items()
        assert items[0]._child.children[0].placeholder == "Search applications..."
        assert isinstance(items, list)

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            d = ApplicationsApplet(size)
            pixbuf = d.create_icon(size)
            assert pixbuf is not None

    def test_search_filters_categories_and_submenus(self, monkeypatch):
        self._fake_gtk(monkeypatch)

        firefox = MagicMock()
        firefox.get_display_name.return_value = "Firefox"
        firefox.get_icon.return_value = None
        chrome = MagicMock()
        chrome.get_display_name.return_value = "Google Chrome"
        chrome.get_icon.return_value = None
        writer = MagicMock()
        writer.get_display_name.return_value = "LibreOffice Writer"
        writer.get_icon.return_value = None
        monkeypatch.setattr(
            applications_applet_mod,
            "_build_app_categories",
            lambda: {
                "Internet": [firefox, chrome],
                "Office": [writer],
            },
        )

        applet = ApplicationsApplet(48)
        items = applet.get_menu_items()
        search_entry = items[0]._child.children[0]
        internet_item = items[2]
        office_item = items[3]

        search_entry.emit("map")
        assert search_entry.focused is True
        assert [
            child.get_label() for child in internet_item.get_submenu().get_children()
        ] == [
            "Firefox",
            "Google Chrome",
        ]
        assert [
            child.get_label() for child in office_item.get_submenu().get_children()
        ] == [
            "LibreOffice Writer",
        ]
        assert internet_item.get_submenu().shown is True
        assert office_item.get_submenu().shown is True

        search_entry.set_text("fire")
        search_entry.emit("changed")

        assert internet_item.visible is True
        assert office_item.visible is False
        assert [
            child.get_label() for child in internet_item.get_submenu().get_children()
        ] == [
            "Firefox",
        ]
        assert internet_item.get_submenu().shown is True

        search_entry.set_text("")
        search_entry.emit("changed")

        assert internet_item.visible is True
        assert office_item.visible is True
        assert len(internet_item.get_submenu().get_children()) == 2
