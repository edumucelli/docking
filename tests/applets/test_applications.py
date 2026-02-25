"""Tests for the Applications applet."""

from unittest.mock import MagicMock, patch

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
            "docking.applets.applications.Gio.AppInfo.get_all",
            return_value=[mock_app],
        ):
            cats = _build_app_categories()
        # Hidden app should not appear in any category
        total = sum(len(apps) for apps in cats.values())
        assert total == 0


class TestApplicationsApplet:
    def test_creates_with_icon(self):
        d = ApplicationsApplet(48)
        assert d.item.icon is not None

    def test_no_click_action(self):
        d = ApplicationsApplet(48)
        # on_clicked is inherited no-op from Applet base
        d.on_clicked()  # should not crash

    def test_menu_returns_items(self):
        d = ApplicationsApplet(48)
        items = d.get_menu_items()
        # Should have at least some categories on a real system
        assert isinstance(items, list)

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            d = ApplicationsApplet(size)
            pixbuf = d.create_icon(size)
            assert pixbuf is not None
