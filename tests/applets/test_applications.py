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

            def append(self, item) -> None:
                self.children.append(item)

        class _FakeMenuItem:
            def __init__(self, label: str) -> None:
                self._label = label
                self._submenu = None
                self._signals: dict[str, list[object]] = {}

            def get_label(self) -> str:
                return self._label

            def connect(self, signal: str, callback) -> None:
                self._signals.setdefault(signal, []).append(callback)

            def set_submenu(self, submenu) -> None:
                self._submenu = submenu

        monkeypatch.setattr(
            applications_applet_mod,
            "Gtk",
            SimpleNamespace(Menu=_FakeMenu),
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
        # Should have at least some categories on a real system
        assert isinstance(items, list)

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            d = ApplicationsApplet(size)
            pixbuf = d.create_icon(size)
            assert pixbuf is not None
