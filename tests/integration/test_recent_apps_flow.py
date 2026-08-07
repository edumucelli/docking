"""Integration tests for the recent apps end-to-end flow.

Exercises the model, config, launcher, and recent_docs modules together.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from docking.applets.services import AppletServices
from docking.core.config import Config
from docking.platform.applications.running import RunningAppInfo
from docking.platform.applications.types import (
    ApplicationInfo,
    ApplicationLocation,
    ApplicationOrigin,
)
from docking.platform.model import DockModel


def _make_running(**kw) -> RunningAppInfo:
    defaults = {"count": 1, "active": False, "urgent": False}
    return RunningAppInfo(**(defaults | kw))


def _make_launcher(*desktop_ids: str):
    launcher = MagicMock()
    infos = {}
    for did in desktop_ids:
        stem = did.removesuffix(".desktop")
        infos[did] = ApplicationInfo(
            desktop_id=did,
            name=stem,
            declared_icon="test-icon",
            wm_class=stem,
            exec_line=stem,
            origin=ApplicationOrigin.INSTALLED,
            location=ApplicationLocation.SANDBOX,
            desktop_file=None,
            executable_path=None,
            aliases=(stem,),
            visible=True,
            has_gio_source=False,
        )

    def resolve(desktop_id, **_kwargs):
        return infos.get(desktop_id)

    launcher.resolve.side_effect = resolve
    launcher.load_icon.return_value = MagicMock()
    return launcher


class TestRecentAppsFullFlow:
    """End-to-end: app launched, closed, appears in recent, pinned, etc."""

    def test_launch_close_reappear_flow(self):
        """App is launched, closed, shown as recent, then relaunched."""
        config = Config()
        config.show_recent_apps = True
        config.recent_apps_max = 5
        config.recent_apps_retention_days = 14
        config.pinned = []
        launcher = _make_launcher("firefox.desktop")
        model = DockModel(config, launcher, AppletServices())

        # App appears running
        model.update_running({"firefox.desktop": _make_running()})
        assert any(
            i.desktop_id == "firefox.desktop" and i.is_running
            for i in model.visible_items()
        )

        # App closes
        model.update_running({})
        items = model.visible_items()
        recent = [i for i in items if i.is_recent]
        assert any(i.desktop_id == "firefox.desktop" for i in recent)

        # App relaunches
        model.update_running({"firefox.desktop": _make_running()})
        items = model.visible_items()
        ff = next(i for i in items if i.desktop_id == "firefox.desktop")
        assert ff.is_running
        assert not ff.is_recent  # Not in recent section while running

    def test_recent_app_pinning_promotes_to_persistent(self):
        """Pinning a recent app makes it a pinned (persistent) item."""
        config = Config()
        config.show_recent_apps = True
        config.recent_apps_max = 10
        config.recent_apps_retention_days = 30
        config.pinned = []
        launcher = _make_launcher("gedit.desktop")
        model = DockModel(config, launcher, AppletServices())

        # Launch and close to get it in recent
        model.update_running({"gedit.desktop": _make_running()})
        model.update_running({})

        recent_items = [i for i in model.visible_items() if i.is_recent]
        assert len(recent_items) >= 1
        assert any(i.desktop_id == "gedit.desktop" for i in recent_items)

        # Pin it
        model.pin_item("gedit.desktop")
        pinned_ids = [i.desktop_id for i in model.visible_items() if i.is_pinned]
        assert "gedit.desktop" in pinned_ids

    def test_disabled_recent_apps_clears_on_rebuild(self):
        """When recent apps is disabled, the section disappears."""
        config = Config(
            show_recent_apps=True,
            recent_apps_max=5,
            recent_apps_retention_days=14,
            pinned=[],
        )
        # Set recent_apps via constructor won't work directly, use the
        # model's mechanism
        launcher = _make_launcher("old.desktop")
        model = DockModel(config, launcher, AppletServices())
        # Start with recent apps enabled and an app in recent
        model.update_running({"old.desktop": _make_running()})
        model.update_running({})

        items_before = model.visible_items()
        assert any(i.is_recent for i in items_before)

        config.show_recent_apps = False
        model.rebuild_recent_apps()

        items_after = model.visible_items()
        assert not any(i.is_recent for i in items_after)

    def test_unresolvable_recent_entry_is_skipped(self):
        """A recent app entry for a nonexistent launcher is skipped gracefully."""
        config = Config()
        config.show_recent_apps = True
        config.recent_apps_max = 10
        config.recent_apps_retention_days = 30
        config.pinned = []
        config.recent_apps = [
            {"desktop_id": "ghost.desktop", "last_closed": 999999},
        ]
        launcher = _make_launcher()  # No launchers
        model = DockModel(config, launcher, AppletServices())

        items = model.visible_items()
        recent = [i for i in items if i.is_recent]
        assert len(recent) == 0  # Unresolvable entry skipped

    def test_find_by_desktop_id_in_recent_section(self):
        """find_by_desktop_id works for items in the recent section."""
        config = Config(
            show_recent_apps=True,
            recent_apps_max=10,
            recent_apps_retention_days=30,
            pinned=[],
        )
        launcher = _make_launcher("gedit.desktop")
        model = DockModel(config, launcher, AppletServices())
        # Get gedit into recent by launching and closing it
        model.update_running({"gedit.desktop": _make_running()})
        model.update_running({})

        item = model.find_by_desktop_id("gedit.desktop")
        assert item is not None
        assert item.is_recent

    def test_config_save_includes_recent_apps(self, tmp_path):
        """Saving config persists recent_apps list."""
        config = Config()
        config.show_recent_apps = True
        config.recent_apps_max = 10
        config.recent_apps_retention_days = 30
        config.recent_apps = [
            {"desktop_id": "app.desktop", "last_closed": 1000},
        ]
        path = tmp_path / "dock.json"
        config.save(path)

        import json

        data = json.loads(path.read_text())
        assert data.get("show_recent_apps") is True
        assert data.get("recent_apps_max") == 10
        assert data.get("recent_apps_retention_days") == 30
        assert len(data.get("recent_apps", [])) == 1
