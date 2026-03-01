"""Tests for the dock data model."""

import sys
from unittest.mock import MagicMock

# Mock gi before importing dock_model
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.platform.model import DockItem, DockModel  # noqa: E402


def _make_launcher(*desktop_ids: str):
    """Create a mock Launcher that resolves given desktop IDs."""
    launcher = MagicMock()
    infos = {}
    for did in desktop_ids:
        info = MagicMock()
        info.desktop_id = did
        info.name = did.removesuffix(".desktop")
        info.icon_name = "test-icon"
        info.wm_class = did.removesuffix(".desktop")
        infos[did] = info

    def resolve(desktop_id):
        return infos.get(desktop_id)

    launcher.resolve.side_effect = resolve
    launcher.load_icon.return_value = MagicMock()  # fake pixbuf
    return launcher


def _make_config(pinned: list[str]):
    config = MagicMock()
    config.pinned = list(pinned)
    config.icon_size = 48
    config.zoom_percent = 2.0
    return config


class TestDockModelInit:
    def test_loads_pinned_items(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        # When
        model = DockModel(config, launcher)
        items = model.visible_items()
        # Then
        assert len(items) == 2
        assert items[0].desktop_id == "a.desktop"
        assert items[1].desktop_id == "b.desktop"
        assert all(it.is_pinned for it in items)

    def test_skips_unresolvable_desktop_ids(self):
        # Given
        config = _make_config(["a.desktop", "missing.desktop"])
        launcher = _make_launcher("a.desktop")
        # When
        model = DockModel(config, launcher)
        # Then
        assert len(model.visible_items()) == 1

    def test_find_by_wm_class(self):
        # Given
        config = _make_config(["firefox.desktop"])
        launcher = _make_launcher("firefox.desktop")
        model = DockModel(config, launcher)
        # When
        found = model.find_by_wm_class("firefox")
        # Then
        assert found is not None
        assert found.desktop_id == "firefox.desktop"

    def test_find_by_wm_class_case_insensitive(self):
        # Given
        config = _make_config(["firefox.desktop"])
        launcher = _make_launcher("firefox.desktop")
        model = DockModel(config, launcher)
        # When
        found = model.find_by_wm_class("Firefox")
        # Then
        assert found is not None
        assert found.desktop_id == "firefox.desktop"

    def test_find_by_wm_class_not_found(self):
        # Given
        config = _make_config(["firefox.desktop"])
        launcher = _make_launcher("firefox.desktop")
        model = DockModel(config, launcher)
        # When
        found = model.find_by_wm_class("chromium")
        # Then
        assert found is None

    def test_empty_pinned(self):
        # Given
        config = _make_config([])
        launcher = _make_launcher()
        # When
        model = DockModel(config, launcher)
        # Then
        assert model.visible_items() == []


class TestUpdateRunning:
    def test_marks_pinned_as_running(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        # When
        model.update_running({"a.desktop": {"count": 2, "active": True}})
        # Then
        item = model.visible_items()[0]
        assert item.is_running
        assert item.is_active
        assert item.instance_count == 2

    def test_adds_transient_for_unknown_running(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)
        # When
        model.update_running(
            {
                "a.desktop": {"count": 1, "active": False},
                "b.desktop": {"count": 1, "active": True},
            }
        )
        # Then
        items = model.visible_items()
        assert len(items) == 2
        assert items[1].desktop_id == "b.desktop"
        assert not items[1].is_pinned
        assert items[1].is_running

    def test_removes_transient_when_no_longer_running(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)
        model.update_running({"b.desktop": {"count": 1, "active": False}})
        assert len(model.visible_items()) == 2
        # When
        model.update_running({})
        # Then
        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "a.desktop"

    def test_resets_running_state_on_update(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        model.update_running({"a.desktop": {"count": 1, "active": True}})
        assert model.visible_items()[0].is_running
        # When
        model.update_running({})
        # Then
        assert not model.visible_items()[0].is_running


class TestPinUnpin:
    def test_pin_transient_item(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)
        model.update_running({"b.desktop": {"count": 1, "active": False}})
        # When
        model.pin_item("b.desktop")
        # Then
        items = model.visible_items()
        assert len(items) == 2
        assert items[1].is_pinned
        assert "b.desktop" in config.pinned

    def test_unpin_running_becomes_transient(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        model.update_running({"a.desktop": {"count": 1, "active": False}})
        # When
        model.unpin_item("a.desktop")
        # Then
        items = model.visible_items()
        assert len(items) == 1
        assert not items[0].is_pinned
        assert "a.desktop" not in config.pinned

    def test_unpin_not_running_removes(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)
        # When
        model.unpin_item("b.desktop")
        # Then
        assert len(model.visible_items()) == 1


class TestReorder:
    def test_reorder_pinned(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop", "c.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop", "c.desktop")
        model = DockModel(config, launcher)
        # When
        model.reorder(0, 2)
        # Then
        ids = [it.desktop_id for it in model.visible_items()]
        assert ids == ["b.desktop", "c.desktop", "a.desktop"]
        assert config.pinned == ["b.desktop", "c.desktop", "a.desktop"]

    def test_reorder_out_of_bounds_noop(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        # When
        model.reorder(0, 5)  # out of bounds
        # Then
        assert len(model.visible_items()) == 1


class TestReorderVisible:
    def test_pinned_items_list_accessible(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        # When
        model = DockModel(config, launcher)
        # Then
        assert isinstance(model.pinned_items, list)
        assert all(isinstance(it, DockItem) for it in model.pinned_items)
        assert len(model.pinned_items) == 2

    def test_sync_and_notify(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)
        callback = MagicMock()
        model.on_change = callback
        # When
        model.pinned_items.reverse()
        model.sync_pinned_to_config()
        model.notify()
        # Then
        assert config.pinned == ["b.desktop", "a.desktop"]
        callback.assert_called_once()

    def test_reorder_pinned_items(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop", "c.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop", "c.desktop")
        model = DockModel(config, launcher)
        # When
        model.reorder_visible(0, 2)
        # Then
        ids = [it.desktop_id for it in model.visible_items()]
        assert ids == ["b.desktop", "c.desktop", "a.desktop"]

    def test_reorder_auto_pins_transient(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)
        model.update_running({"b.desktop": {"count": 1, "active": False}})
        assert len(model.visible_items()) == 2
        assert not model.visible_items()[1].is_pinned
        # When
        model.reorder_visible(1, 0)
        # Then
        items = model.visible_items()
        assert items[0].desktop_id == "b.desktop"
        assert items[0].is_pinned
        assert "b.desktop" in config.pinned

    def test_reorder_both_transients(self):
        # Given
        config = _make_config([])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)
        model.update_running(
            {
                "a.desktop": {"count": 1, "active": False},
                "b.desktop": {"count": 1, "active": False},
            }
        )
        assert len(model.visible_items()) == 2
        # When
        model.reorder_visible(1, 0)
        # Then
        items = model.visible_items()
        assert all(it.is_pinned for it in items)
        assert len(config.pinned) == 2

    def test_reorder_visible_out_of_bounds_noop(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        # When
        model.reorder_visible(0, 5)
        # Then
        assert len(model.visible_items()) == 1


class TestCallbacks:
    def test_on_change_fires(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        callback = MagicMock()
        model.on_change = callback
        # When
        model.update_running({"a.desktop": {"count": 1, "active": False}})
        # Then
        callback.assert_called_once()


class TestAppletLifecycleIntegration:
    def test_add_applet_and_remove_applet_updates_config_and_notifies(
        self, monkeypatch
    ):
        # Given
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher)
        callback = MagicMock()
        model.on_change = callback

        fake_item = DockItem(desktop_id="applet://session", name="Session")
        fake_applet = MagicMock()
        fake_applet.item = fake_item

        class FakeAppletClass:
            def __new__(cls, icon_size, config):
                return fake_applet

        import docking.applets as applets_mod
        import docking.applets.identity as identity_mod

        monkeypatch.setattr(
            applets_mod,
            "get_registry",
            lambda: {identity_mod.AppletId.SESSION: FakeAppletClass},
        )
        # When
        model.add_applet("session")
        # Then
        assert fake_applet.start.called
        assert "applet://session" in config.pinned
        assert config.save.called
        assert callback.called

        # Given
        callback.reset_mock()
        # When
        model.remove_applet("applet://session")
        # Then
        fake_applet.stop.assert_called_once()
        assert "applet://session" not in config.pinned
        assert callback.called

    def test_add_separator_assigns_instance_and_inserts_at_index(self, monkeypatch):
        # Given
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher)

        import docking.applets as applets_mod
        import docking.applets.identity as identity_mod

        created: list[MagicMock] = []

        class FakeSeparatorClass:
            def __new__(cls, icon_size, config):
                app = MagicMock()
                app.item = DockItem(desktop_id="applet://separator", name="Separator")
                created.append(app)
                return app

        monkeypatch.setattr(
            applets_mod,
            "get_registry",
            lambda: {identity_mod.AppletId.SEPARATOR: FakeSeparatorClass},
        )
        # When
        model.add_separator(index=0)
        # Then
        assert len(model.pinned_items) == 1
        assert model.pinned_items[0].desktop_id.startswith("applet://separator#")
        assert created[0].start.called
        assert config.save.called

    def test_start_stop_applets_and_get_applet(self):
        # Given
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher)
        applet = MagicMock()
        applet.item = DockItem(desktop_id="applet://x", name="X")
        model._applets["applet://x"] = applet
        # When
        model.start_applets()
        model.stop_applets()
        found = model.get_applet("applet://x")
        # Then
        applet.start.assert_called_once()
        applet.stop.assert_called_once()
        assert found is applet

    def test_find_by_desktop_id_and_unpin_applet_route(self, monkeypatch):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        item = model.find_by_desktop_id("a.desktop")
        # Then
        assert item is not None

        # Given
        remove = MagicMock()
        monkeypatch.setattr(model, "remove_applet", remove)
        # When
        model.unpin_item("applet://session")
        # Then
        remove.assert_called_once_with(desktop_id="applet://session")


class TestDockItemAnimationFields:
    def test_default_timestamps_zero(self):
        # Given / When
        item = DockItem(desktop_id="test.desktop")
        # Then
        assert item.last_clicked == 0
        assert item.last_launched == 0
        assert item.last_urgent == 0
        assert item.is_urgent is False

    def test_urgent_state_tracked(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        # When
        model.update_running(
            {"a.desktop": {"count": 1, "active": False, "urgent": True}}
        )
        # Then
        item = model.visible_items()[0]
        assert item.is_urgent is True
        assert item.last_urgent != 0  # timestamp was set

    def test_urgent_timestamp_set_only_on_transition(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        # When
        model.update_running(
            {"a.desktop": {"count": 1, "active": False, "urgent": True}}
        )
        first_ts = model.visible_items()[0].last_urgent
        # When
        model.update_running(
            {"a.desktop": {"count": 1, "active": False, "urgent": True}}
        )
        second_ts = model.visible_items()[0].last_urgent
        # Then
        assert second_ts is first_ts

    def test_urgent_clears(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        model.update_running(
            {"a.desktop": {"count": 1, "active": False, "urgent": True}}
        )
        # When
        model.update_running(
            {"a.desktop": {"count": 1, "active": False, "urgent": False}}
        )
        # Then
        assert model.visible_items()[0].is_urgent is False

    def test_click_and_launch_timestamps_independent(self):
        # Given
        item = DockItem(desktop_id="test.desktop")
        # When
        item.last_clicked = 12345
        # Then
        assert item.last_launched == 0
        # When
        item.last_launched = 67890
        # Then
        assert item.last_clicked == 12345
