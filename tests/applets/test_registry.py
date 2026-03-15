"""Tests for the applet registry and shared utilities."""

import importlib.util
from typing import cast
from unittest.mock import patch

import pytest

from docking.applets import get_applet_catalog, load_applet_class
from docking.applets.base import Applet, load_theme_icon, load_theme_icon_centered
from docking.applets.identity import AppletId


class TestAppletCatalog:
    def test_returns_dict(self):
        catalog = get_applet_catalog()
        assert isinstance(catalog, dict)

    def test_cached_returns_same_object(self):
        c1 = get_applet_catalog()
        c2 = get_applet_catalog()
        assert c1 is c2

    def test_does_not_import_modules(self):
        get_applet_catalog.cache_clear()
        with patch("docking.applets.import_module", side_effect=AssertionError):
            catalog = get_applet_catalog()

        assert AppletId.CLOCK in catalog

    def test_all_values_are_catalog_entries(self):
        for applet_id, entry in get_applet_catalog().items():
            assert entry.applet_id == applet_id
            assert entry.name
            assert entry.module_path
            assert entry.class_name

    def test_contains_clock(self):
        assert "clock" in get_applet_catalog()

    def test_contains_trash(self):
        assert "trash" in get_applet_catalog()

    def test_contains_desktop(self):
        assert "desktop" in get_applet_catalog()

    def test_contains_cpumonitor(self):
        assert "cpumonitor" in get_applet_catalog()

    def test_contains_battery(self):
        assert "battery" in get_applet_catalog()

    def test_contains_weather(self):
        assert "weather" in get_applet_catalog()

    def test_contains_session(self):
        assert "session" in get_applet_catalog()

    def test_contains_calendar(self):
        assert "calendar" in get_applet_catalog()

    def test_contains_workspaces(self):
        assert "workspaces" in get_applet_catalog()

    def test_contains_music(self):
        assert "music" in get_applet_catalog()

    def test_contains_notifications(self):
        assert "notifications" in get_applet_catalog()

    def test_contains_bluetooth(self):
        assert "bluetooth" in get_applet_catalog()

    def test_contains_powerprofiles(self):
        assert "powerprofiles" in get_applet_catalog()

    def test_contains_stretchcoach(self):
        assert "stretchcoach" in get_applet_catalog()

    def test_contains_trivia(self):
        assert "trivia" in get_applet_catalog()

    def test_contains_todayinhistory_when_available(self):
        if importlib.util.find_spec("docking.applets.todayinhistory") is None:
            pytest.skip("Today in History applet is not available in this checkout")

        assert "todayinhistory" in get_applet_catalog()


class TestLoadAppletClass:
    def test_all_values_are_applet_subclasses(self):
        for applet_id in get_applet_catalog():
            cls = load_applet_class(applet_id)
            assert issubclass(cls, Applet), f"{applet_id}: {cls} not a Applet"

    def test_cached_returns_same_object(self):
        load_applet_class.cache_clear()
        c1 = load_applet_class(AppletId.CLOCK)
        c2 = load_applet_class(AppletId.CLOCK)
        assert c1 is c2

    def test_unknown_applet_returns_none(self):
        assert load_applet_class(cast(AppletId, "unknown")) is None

    def test_catalog_name_matches_loaded_class_name(self):
        for applet_id, entry in get_applet_catalog().items():
            cls = load_applet_class(applet_id)
            assert cls is not None
            assert cls.name == entry.name


class TestLoadThemeIcon:
    def test_loads_known_icon(self):
        pixbuf = load_theme_icon(name="user-trash", size=48)
        assert pixbuf is not None
        assert pixbuf.get_width() == 48

    def test_returns_none_for_unknown(self):
        assert load_theme_icon(name="nonexistent-icon-xyz", size=48) is None

    def test_centered_returns_square(self):
        pixbuf = load_theme_icon_centered(name="user-trash", size=48)
        assert pixbuf is not None
        assert pixbuf.get_width() == pixbuf.get_height()

    def test_centered_returns_none_for_unknown(self):
        assert load_theme_icon_centered(name="nonexistent-icon-xyz", size=48) is None

    def test_uses_bundled_fallback_for_known_icon_when_theme_unavailable(self):
        with patch("docking.applets.base._icon_theme_candidates", return_value=()):
            pixbuf = load_theme_icon(name="view-app-grid", size=48)
        assert pixbuf is not None
        assert pixbuf.get_width() == 48

    def test_unknown_icon_still_none_when_theme_unavailable(self):
        with patch("docking.applets.base._icon_theme_candidates", return_value=()):
            pixbuf = load_theme_icon(name="nonexistent-icon-xyz", size=48)
        assert pixbuf is None
