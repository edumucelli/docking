"""Tests for the applet registry and shared utilities."""

from unittest.mock import patch

from docking.applets import get_registry
from docking.applets.base import Applet, load_theme_icon, load_theme_icon_centered


class TestRegistry:
    def test_returns_dict(self):
        registry = get_registry()
        assert isinstance(registry, dict)

    def test_all_values_are_applet_subclasses(self):
        for applet_id, cls in get_registry().items():
            assert issubclass(cls, Applet), f"{applet_id}: {cls} not a Applet"

    def test_contains_clock(self):
        assert "clock" in get_registry()

    def test_contains_trash(self):
        assert "trash" in get_registry()

    def test_contains_desktop(self):
        assert "desktop" in get_registry()

    def test_contains_cpumonitor(self):
        assert "cpumonitor" in get_registry()

    def test_contains_battery(self):
        assert "battery" in get_registry()

    def test_cached_returns_same_object(self):
        r1 = get_registry()
        r2 = get_registry()
        assert r1 is r2

    def test_contains_weather(self):
        assert "weather" in get_registry()

    def test_contains_session(self):
        assert "session" in get_registry()

    def test_contains_calendar(self):
        assert "calendar" in get_registry()

    def test_contains_workspaces(self):
        assert "workspaces" in get_registry()


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
