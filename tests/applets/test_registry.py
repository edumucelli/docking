"""Tests for the applet registry and shared utilities."""

import importlib.util
import logging
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import docking.applets as applets_mod
from docking.applets import get_applet_catalog, load_applet_class
from docking.applets.base import Applet, load_theme_icon


@pytest.fixture(autouse=True)
def _clear_registry_caches():
    get_applet_catalog.cache_clear()
    load_applet_class.cache_clear()
    yield
    get_applet_catalog.cache_clear()
    load_applet_class.cache_clear()


class TestAppletCatalog:
    def test_returns_dict(self):
        catalog = get_applet_catalog()
        assert isinstance(catalog, dict)

    def test_cached_returns_same_object(self):
        c1 = get_applet_catalog()
        c2 = get_applet_catalog()
        assert c1 is c2

    def test_all_entries_have_required_fields(self):
        for applet_id, entry in get_applet_catalog().items():
            assert entry.id == applet_id
            assert entry.name
            assert entry.category

    def test_contains_clock(self):
        assert "clock" in get_applet_catalog()

    def test_contains_trash(self):
        assert "trash" in get_applet_catalog()

    def test_contains_desktop(self):
        assert "desktop" in get_applet_catalog()

    def test_contains_devices(self):
        assert "devices" in get_applet_catalog()

    def test_contains_systemmonitor(self):
        assert "systemmonitor" in get_applet_catalog()

    def test_contains_battery(self):
        assert "battery" in get_applet_catalog()

    def test_contains_weather_when_available(self):
        if importlib.util.find_spec("openmeteo_requests") is None:
            pytest.skip("openmeteo_requests not installed")
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

    def test_contains_currencyfx(self):
        assert "currencyfx" in get_applet_catalog()

    def test_contains_crypto(self):
        assert "crypto" in get_applet_catalog()

    def test_contains_micshield(self):
        assert "micshield" in get_applet_catalog()

    def test_contains_hackernews(self):
        assert "hackernews" in get_applet_catalog()

    def test_contains_reddit(self):
        assert "reddit" in get_applet_catalog()
        assert load_applet_class("reddit").__name__ == "RedditApplet"

    def test_contains_thermals(self):
        assert "thermals" in get_applet_catalog()

    def test_contains_docker(self):
        assert "docker" in get_applet_catalog()

    def test_logs_warning_for_package_missing_init_py(
        self, tmp_path, monkeypatch, caplog
    ):
        fake_applets = tmp_path / "applets"
        fake_applets.mkdir()
        (fake_applets / "broken").mkdir()
        monkeypatch.setattr(applets_mod, "__file__", str(fake_applets / "__init__.py"))
        get_applet_catalog.cache_clear()

        with caplog.at_level(logging.WARNING, logger=applets_mod.__name__):
            catalog = get_applet_catalog()

        assert catalog == {}
        assert "missing __init__.py" in caplog.text
        get_applet_catalog.cache_clear()

    def test_logs_warning_for_package_missing_meta(self, tmp_path, monkeypatch, caplog):
        fake_applets = tmp_path / "applets"
        fake_applets.mkdir()
        pkg = fake_applets / "broken"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("", encoding="utf-8")
        monkeypatch.setattr(applets_mod, "__file__", str(fake_applets / "__init__.py"))
        monkeypatch.setattr(
            applets_mod, "import_module", lambda _name: SimpleNamespace()
        )
        get_applet_catalog.cache_clear()

        with caplog.at_level(logging.WARNING, logger=applets_mod.__name__):
            catalog = get_applet_catalog()

        assert catalog == {}
        assert "missing meta declaration" in caplog.text
        get_applet_catalog.cache_clear()

    def test_catalog_discovery_does_not_import_applet_modules(self):
        script = """
import json
import sys

from docking.applets import get_applet_catalog

get_applet_catalog()
loaded = sorted(
    name
    for name in sys.modules
    if name.startswith("docking.applets.") and name.endswith(".applet")
)
print(json.dumps(loaded))
"""

        result = subprocess.run(
            [sys.executable, "-c", script],
            check=True,
            capture_output=True,
            text=True,
        )

        assert result.stdout.strip() == "[]"


class TestLoadAppletClass:
    def test_all_values_are_applet_subclasses(self):
        for applet_id in get_applet_catalog():
            cls = load_applet_class(applet_id)
            assert issubclass(cls, Applet), f"{applet_id}: {cls} not a Applet"

    def test_cached_returns_same_object(self):
        load_applet_class.cache_clear()
        c1 = load_applet_class("clock")
        c2 = load_applet_class("clock")
        assert c1 is c2

    def test_unknown_applet_returns_none(self):
        assert load_applet_class("unknown") is None

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

    def test_uses_bundled_fallback_for_known_icon_when_theme_unavailable(self):
        with patch("docking.applets.base._icon_theme_candidates", return_value=()):
            pixbuf = load_theme_icon(name="view-app-grid", size=48)
        assert pixbuf is not None
        assert pixbuf.get_width() == 48

    def test_unknown_icon_still_none_when_theme_unavailable(self):
        with patch("docking.applets.base._icon_theme_candidates", return_value=()):
            pixbuf = load_theme_icon(name="nonexistent-icon-xyz", size=48)
        assert pixbuf is None
