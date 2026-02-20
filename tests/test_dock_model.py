"""Tests for the dock data model."""

import sys
import pytest
from unittest.mock import MagicMock, patch

# Mock gi before importing dock_model
gi_mock = MagicMock()
gi_mock.require_version = MagicMock()
sys.modules.setdefault("gi", gi_mock)
sys.modules.setdefault("gi.repository", gi_mock.repository)

from docking.dock_model import DockModel, DockItem  # noqa: E402


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
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)
        items = model.visible_items()
        assert len(items) == 2
        assert items[0].desktop_id == "a.desktop"
        assert items[1].desktop_id == "b.desktop"
        assert all(it.is_pinned for it in items)

    def test_skips_unresolvable_desktop_ids(self):
        config = _make_config(["a.desktop", "missing.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        assert len(model.visible_items()) == 1

    def test_empty_pinned(self):
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher)
        assert model.visible_items() == []


class TestUpdateRunning:
    def test_marks_pinned_as_running(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)

        model.update_running({"a.desktop": {"count": 2, "active": True}})
        item = model.visible_items()[0]
        assert item.is_running
        assert item.is_active
        assert item.instance_count == 2

    def test_adds_transient_for_unknown_running(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)

        model.update_running({
            "a.desktop": {"count": 1, "active": False},
            "b.desktop": {"count": 1, "active": True},
        })
        items = model.visible_items()
        assert len(items) == 2
        assert items[1].desktop_id == "b.desktop"
        assert not items[1].is_pinned
        assert items[1].is_running

    def test_removes_transient_when_no_longer_running(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)

        model.update_running({"b.desktop": {"count": 1, "active": False}})
        assert len(model.visible_items()) == 2

        model.update_running({})
        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "a.desktop"

    def test_resets_running_state_on_update(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)

        model.update_running({"a.desktop": {"count": 1, "active": True}})
        assert model.visible_items()[0].is_running

        model.update_running({})
        assert not model.visible_items()[0].is_running


class TestPinUnpin:
    def test_pin_transient_item(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)

        model.update_running({"b.desktop": {"count": 1, "active": False}})
        model.pin_item("b.desktop")

        items = model.visible_items()
        assert len(items) == 2
        assert items[1].is_pinned
        assert "b.desktop" in config.pinned

    def test_unpin_running_becomes_transient(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)

        model.update_running({"a.desktop": {"count": 1, "active": False}})
        model.unpin_item("a.desktop")

        items = model.visible_items()
        assert len(items) == 1
        assert not items[0].is_pinned
        assert "a.desktop" not in config.pinned

    def test_unpin_not_running_removes(self):
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher)

        model.unpin_item("b.desktop")
        assert len(model.visible_items()) == 1


class TestReorder:
    def test_reorder_pinned(self):
        config = _make_config(["a.desktop", "b.desktop", "c.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop", "c.desktop")
        model = DockModel(config, launcher)

        model.reorder(0, 2)
        ids = [it.desktop_id for it in model.visible_items()]
        assert ids == ["b.desktop", "c.desktop", "a.desktop"]
        assert config.pinned == ["b.desktop", "c.desktop", "a.desktop"]

    def test_reorder_out_of_bounds_noop(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)
        model.reorder(0, 5)  # out of bounds
        assert len(model.visible_items()) == 1


class TestCallbacks:
    def test_on_change_fires(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher)

        callback = MagicMock()
        model.on_change = callback

        model.update_running({"a.desktop": {"count": 1, "active": False}})
        callback.assert_called_once()
