"""Tests for the dock data model."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

try:
    import gi  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    gi_mock = MagicMock()
    gi_mock.require_version = MagicMock()
    sys.modules.setdefault("gi", gi_mock)
    sys.modules.setdefault("gi.repository", gi_mock.repository)

import docking.platform.model as model_mod
from docking.applets.services import AppletServices
from docking.core.config import PinnedEntry
from docking.core.icons import CUSTOM_ICON_PATH_KEY, ICON_SOURCE_PREF_KEY, IconSource
from docking.core.items import APP_KIND, FILE_KIND, FOLDER_KIND
from docking.platform.desktop_entries import DesktopInfo, GeneratedDesktopEntry
from docking.platform.model import DockItem, DockModel, LauncherEntryState
from docking.platform.running import RunningAppInfo, RuntimeAppIdentity


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
        info.exec_line = did.removesuffix(".desktop")
        infos[did] = info

    def resolve(desktop_id, **_kwargs):
        return infos.get(desktop_id)

    launcher.resolve.side_effect = resolve
    launcher.load_icon.return_value = MagicMock()  # fake pixbuf
    launcher.load_desktop_icon.return_value = MagicMock()
    launcher.load_icon_file.return_value = None
    return launcher


def _make_config(pinned: list[str], *, show_recent_apps: bool = False):
    config = MagicMock()
    config.pinned = list(pinned)
    config.icon_size = 48
    config.scaled_icon_size = 48
    config.zoom_percent = 2.0
    config.anchor_applets = False
    config.anchor_files = False
    config.item_prefs = {}
    config.show_launcher_badges = True
    config.show_launcher_progress = True
    config.show_recent_apps = show_recent_apps
    config.recent_apps_max = 5
    config.recent_apps_retention_days = 14
    config.recent_apps = []
    return config


def _running(
    *, count: int = 1, active: bool = False, urgent: bool = False
) -> RunningAppInfo:
    return RunningAppInfo(count=count, active=active, urgent=urgent)


class TestDockModelInit:
    def test_loads_pinned_items(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        # When
        model = DockModel(config, launcher, AppletServices())
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
        model = DockModel(config, launcher, AppletServices())
        # Then
        assert len(model.visible_items()) == 1

    def test_empty_pinned(self):
        # Given
        config = _make_config([])
        launcher = _make_launcher()
        # When
        model = DockModel(config, launcher, AppletServices())
        # Then
        assert model.visible_items() == []


class TestUpdateRunning:
    def test_marks_pinned_as_running(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        # When
        model.update_running({"a.desktop": _running(count=2, active=True)})
        # Then
        item = model.visible_items()[0]
        assert item.is_running
        assert item.is_active
        assert item.instance_count == 2

    def test_adds_transient_for_unknown_running(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        # When
        model.update_running(
            {
                "a.desktop": _running(),
                "b.desktop": _running(active=True),
            }
        )
        # Then
        items = model.visible_items()
        assert len(items) == 2
        assert items[1].desktop_id == "b.desktop"
        assert not items[1].is_pinned
        assert items[1].is_running

    def test_runtime_identity_builds_launchable_transient(self, tmp_path):
        executable = tmp_path / "tool-v2" / "bin" / "tool"
        executable.parent.mkdir(parents=True)
        executable.write_bytes(b"\x7fELF")
        runtime = RuntimeAppIdentity(
            desktop_id="docking-generated-tool-runtime.desktop",
            executable_path=str(executable),
            name="Shared Tool",
            icon_name="shared-tool",
            wm_class="SharedTool",
        )
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())

        model.update_running(
            {
                runtime.desktop_id: RunningAppInfo(
                    count=1,
                    runtime_app=runtime,
                )
            }
        )

        item = model.visible_items()[0]
        assert item.desktop_id == runtime.desktop_id
        assert item.name == "Shared Tool"
        assert item.wm_class == "SharedTool"
        assert item.runtime_executable == str(executable)

    def test_removes_transient_when_no_longer_running(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.update_running({"b.desktop": _running()})
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
        model = DockModel(config, launcher, AppletServices())
        model.update_running({"a.desktop": _running(active=True)})
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
        model = DockModel(config, launcher, AppletServices())
        model.update_running({"b.desktop": _running()})
        # When
        model.pin_item("b.desktop")
        # Then
        items = model.visible_items()
        assert len(items) == 2
        assert items[1].is_pinned
        assert "b.desktop" in config.pinned
        config.save.assert_called_once()

    def test_pin_runtime_transient_generates_persistent_launcher(
        self, tmp_path, monkeypatch
    ):
        executable = tmp_path / "tool"
        executable.write_bytes(b"\x7fELF")
        runtime_id = model_mod.desktop_entries.generated_desktop_id_for_path(executable)
        runtime = RuntimeAppIdentity(
            desktop_id=runtime_id,
            executable_path=str(executable),
            name="Shared Tool",
            icon_name="shared-tool",
            wm_class="SharedTool",
        )
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
        model.update_running(
            {
                runtime_id: RunningAppInfo(
                    count=1,
                    runtime_app=runtime,
                )
            }
        )
        generated = GeneratedDesktopEntry(
            desktop_id=runtime_id,
            path=tmp_path / runtime_id,
            name="tool",
            icon_name="application-x-executable",
        )
        resolved = DesktopInfo(
            desktop_id=runtime_id,
            name="tool",
            icon_name="application-x-executable",
            wm_class="tool",
            exec_line=str(executable),
        )
        create = MagicMock(return_value=generated)
        monkeypatch.setattr(
            model_mod.desktop_entries,
            "create_desktop_entry_for_executable",
            create,
        )
        launcher.resolve.side_effect = lambda desktop_id, **_: (
            resolved if desktop_id == runtime_id else None
        )

        model.pin_item(runtime_id)

        item = model.visible_items()[0]
        assert item.is_pinned
        assert item.runtime_executable == ""
        assert item.target == runtime_id
        create.assert_called_once_with(
            str(executable),
            startup_wm_class="SharedTool",
        )
        launcher.refresh_desktop_entries.assert_called_once_with()

    def test_unpin_running_becomes_transient(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.update_running({"a.desktop": _running()})
        # When
        model.unpin_item("a.desktop")
        # Then
        items = model.visible_items()
        assert len(items) == 1
        assert not items[0].is_pinned
        assert "a.desktop" not in config.pinned
        config.save.assert_called_once()

    def test_unpin_not_running_removes(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        # When
        model.unpin_item("b.desktop")
        # Then - item is animating out, flush animation to complete removal
        while model.tick_animations():
            pass
        assert len(model.visible_items()) == 1
        config.save.assert_called_once()


class TestCustomIcons:
    def test_pinned_app_applies_custom_icon_on_load(self):
        config = _make_config(["a.desktop"])
        config.item_prefs = {
            "a.desktop": {
                ICON_SOURCE_PREF_KEY: IconSource.CUSTOM.value,
                CUSTOM_ICON_PATH_KEY: "/home/user/a.png",
            }
        }
        launcher = _make_launcher("a.desktop")
        custom_icon = object()
        launcher.load_icon_file.return_value = custom_icon

        model = DockModel(config, launcher, AppletServices())

        item = model.visible_items()[0]
        assert item.icon is custom_icon
        assert item.icon_name == "a.png"
        launcher.load_icon_file.assert_called_once_with(
            path=Path("/home/user/a.png"),
            size=48,
        )

    def test_set_custom_icon_persists_and_refreshes_matching_items(self, tmp_path):
        config = _make_config(["a.desktop"], show_recent_apps=True)
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        pinned = model.visible_items()[0]
        recent = DockItem(
            desktop_id="a.desktop",
            kind=APP_KIND,
            target="a.desktop",
            is_recent=True,
            icon=object(),
        )
        model._recent_apps.append(recent)
        custom_path = tmp_path / "custom.png"
        custom_path.write_bytes(b"not actually decoded in this unit test")
        custom_icon = object()
        launcher.load_icon_file.return_value = custom_icon
        callback = MagicMock()
        model.add_change_listener(callback)

        assert model.set_custom_icon(pinned, custom_path)

        assert config.item_prefs["a.desktop"] == {
            ICON_SOURCE_PREF_KEY: IconSource.CUSTOM.value,
            CUSTOM_ICON_PATH_KEY: str(custom_path),
        }
        assert pinned.icon is custom_icon
        assert recent.icon is custom_icon
        config.save.assert_called_once()
        callback.assert_called_once()

    def test_refresh_item_icons_preserves_runtime_state(self, tmp_path):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        item = model.visible_items()[0]
        item.is_running = True
        item.instance_count = 2
        item.badge_count = 7
        item.progress_visible = True
        item.window_urgent = True
        item.insert_factor = 0.5
        config.item_prefs = {
            "a.desktop": {
                ICON_SOURCE_PREF_KEY: IconSource.CUSTOM.value,
                CUSTOM_ICON_PATH_KEY: str(tmp_path / "custom.png"),
            }
        }
        custom_icon = object()
        launcher.load_icon_file.return_value = custom_icon

        model.refresh_item_icons(item)

        assert item.icon is custom_icon
        assert item.is_running is True
        assert item.instance_count == 2
        assert item.badge_count == 7
        assert item.progress_visible is True
        assert item.window_urgent is True
        assert item.insert_factor == 0.5

    def test_insert_pinned_item_applies_override_and_persists(self):
        config = _make_config([])
        config.item_prefs = {
            "tool.desktop": {
                ICON_SOURCE_PREF_KEY: IconSource.CUSTOM.value,
                CUSTOM_ICON_PATH_KEY: "/home/user/tool.png",
            }
        }
        launcher = _make_launcher("tool.desktop")
        custom_icon = object()
        launcher.load_icon_file.return_value = custom_icon
        model = DockModel(config, launcher, AppletServices())
        item = DockItem(
            desktop_id="tool.desktop",
            kind=APP_KIND,
            target="tool.desktop",
            is_pinned=True,
            icon=object(),
        )

        assert model.insert_pinned_item(item=item, index=0)

        assert model.pinned_items == [item]
        assert item.icon is custom_icon
        assert config.pinned == [PinnedEntry(kind=APP_KIND, target="tool.desktop")]
        config.save.assert_called_once()


class TestReorderVisible:
    def test_pinned_items_list_accessible(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        # When
        model = DockModel(config, launcher, AppletServices())
        # Then
        assert isinstance(model.pinned_items, list)
        assert all(isinstance(it, DockItem) for it in model.pinned_items)
        assert len(model.pinned_items) == 2

    def test_sync_and_notify(self):
        # Given
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        callback = MagicMock()
        model.add_change_listener(callback)
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
        model = DockModel(config, launcher, AppletServices())
        # When
        model.reorder_visible(0, 2)
        # Then
        ids = [it.desktop_id for it in model.visible_items()]
        assert ids == ["b.desktop", "c.desktop", "a.desktop"]
        config.save.assert_called_once()

    def test_reorder_auto_pins_transient(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.update_running({"b.desktop": _running()})
        assert len(model.visible_items()) == 2
        assert not model.visible_items()[1].is_pinned
        # When
        model.reorder_visible(1, 0)
        # Then
        items = model.visible_items()
        assert items[0].desktop_id == "b.desktop"
        assert items[0].is_pinned
        assert "b.desktop" in config.pinned
        config.save.assert_called_once()

    def test_reorder_both_transients(self):
        # Given
        config = _make_config([])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.update_running(
            {
                "a.desktop": _running(),
                "b.desktop": _running(),
            }
        )
        assert len(model.visible_items()) == 2
        # When
        model.reorder_visible(1, 0)
        # Then
        items = model.visible_items()
        assert all(it.is_pinned for it in items)
        assert len(config.pinned) == 2
        config.save.assert_called_once()

    def test_reorder_visible_out_of_bounds_noop(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        # When
        model.reorder_visible(0, 5)
        # Then
        assert len(model.visible_items()) == 1
        config.save.assert_not_called()

    def test_anchor_files_moves_files_and_folders_after_apps(self):
        config = _make_config([])
        config.pinned = [
            PinnedEntry(kind=FILE_KIND, target="file:///tmp/readme.txt"),
            PinnedEntry(kind=APP_KIND, target="a.desktop"),
            PinnedEntry(kind=FOLDER_KIND, target="file:///tmp/docs"),
        ]
        config.anchor_files = True
        launcher = _make_launcher("a.desktop")
        launcher.resolve_file.side_effect = [
            MagicMock(
                target="file:///tmp/readme.txt",
                name="readme.txt",
                icon_name="text-x-generic",
                icon=MagicMock(),
                is_dir=False,
            ),
            MagicMock(
                target="file:///tmp/docs",
                name="docs",
                icon_name="folder",
                icon=MagicMock(),
                is_dir=True,
            ),
        ]

        model = DockModel(config, launcher, AppletServices())

        assert [item.kind for item in model.visible_items()] == [
            APP_KIND,
            FILE_KIND,
            FOLDER_KIND,
        ]


class TestCallbacks:
    def test_change_listener_fires(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        callback = MagicMock()
        model.add_change_listener(callback)
        # When
        model.update_running({"a.desktop": _running()})
        # Then
        callback.assert_called_once()

    def test_removed_change_listener_does_not_fire(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        callback = MagicMock()
        model.add_change_listener(callback)
        model.remove_change_listener(callback)

        model.notify()

        callback.assert_not_called()

    def test_change_listeners_fire_in_registration_order(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        calls: list[str] = []

        model.add_change_listener(lambda: calls.append("first"))
        model.add_change_listener(lambda: calls.append("second"))

        model.notify()

        assert calls == ["first", "second"]

    def test_failing_change_listener_does_not_block_later_listener(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        good = MagicMock()

        def bad() -> None:
            raise RuntimeError("listener failed")

        model.add_change_listener(bad)
        model.add_change_listener(good)

        model.notify()

        good.assert_called_once()

    def test_multiple_failing_change_listeners_do_not_block_later_listener(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        good = MagicMock()

        def first_bad() -> None:
            raise RuntimeError("first failed")

        def second_bad() -> None:
            raise RuntimeError("second failed")

        model.add_change_listener(first_bad)
        model.add_change_listener(second_bad)
        model.add_change_listener(good)

        model.notify()

        good.assert_called_once()

    def test_listener_can_remove_itself_during_notify(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        calls: list[str] = []

        def first() -> None:
            calls.append("first")
            model.remove_change_listener(first)

        def second() -> None:
            calls.append("second")

        model.add_change_listener(first)
        model.add_change_listener(second)

        model.notify()
        model.notify()

        assert calls == ["first", "second", "second"]


class TestAppletLifecycleIntegration:
    def test_loads_pinned_applet_via_lazy_loader(self, monkeypatch):
        config = MagicMock()
        config.pinned = [PinnedEntry(kind="applet", target="applet://session")]
        config.icon_size = 48
        config.zoom_percent = 2.0
        config.anchor_applets = False
        config.anchor_files = False
        config.item_prefs = {}
        launcher = _make_launcher()

        fake_item = DockItem(desktop_id="applet://session", name="Session")
        fake_applet = MagicMock()
        fake_applet.item = fake_item

        import docking.applets as applets_mod

        loader = MagicMock(return_value=lambda icon_size, config: fake_applet)
        monkeypatch.setattr(applets_mod, "load_applet_class", loader)

        model = DockModel(config, launcher, AppletServices())

        loader.assert_called_once_with("session")
        assert model.pinned_items == [fake_item]
        assert model.get_applet("applet://session") is fake_applet

    def test_add_applet_and_remove_applet_updates_config_and_notifies(
        self, monkeypatch
    ):
        # Given
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
        callback = MagicMock()
        model.add_change_listener(callback)

        fake_item = DockItem(desktop_id="applet://session", name="Session")
        fake_applet = MagicMock()
        fake_applet.item = fake_item

        class FakeAppletClass:
            def __new__(cls, icon_size, config):
                return fake_applet

        import docking.applets as applets_mod

        monkeypatch.setattr(
            applets_mod,
            "load_applet_class",
            lambda applet_id: FakeAppletClass if applet_id == "session" else None,
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
        model = DockModel(config, launcher, AppletServices())

        import docking.applets as applets_mod

        created: list[MagicMock] = []

        class FakeSeparatorClass:
            def __new__(cls, icon_size, config):
                app = MagicMock()
                app.item = DockItem(desktop_id="applet://separator", name="Separator")
                created.append(app)
                return app

        monkeypatch.setattr(
            applets_mod,
            "load_applet_class",
            lambda applet_id: FakeSeparatorClass if applet_id == "separator" else None,
        )
        # When
        model.add_separator(index=0)
        # Then
        assert len(model.pinned_items) == 1
        assert model.pinned_items[0].desktop_id.startswith("applet://separator#")
        created[0].apply_prefs.assert_called_once()
        assert created[0].start.called
        assert config.save.called

    def test_removed_separator_keeps_former_visible_position_while_animating(
        self, monkeypatch
    ):
        config = _make_config(["a.desktop", "b.desktop"])
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())

        import docking.applets as applets_mod

        class FakeSeparatorClass:
            def __new__(cls, icon_size, config):
                app = MagicMock()
                app.item = DockItem(desktop_id="applet://separator", name="Separator")
                return app

        monkeypatch.setattr(
            applets_mod,
            "load_applet_class",
            lambda applet_id: FakeSeparatorClass if applet_id == "separator" else None,
        )

        model.add_separator(index=1)
        separator_id = model.pinned_items[1].desktop_id

        model.remove_applet(separator_id)

        assert [item.desktop_id for item in model.visible_items()] == [
            "a.desktop",
            separator_id,
            "b.desktop",
        ]

    def test_start_stop_applets_and_get_applet(self):
        # Given
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
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

    def test_start_applets_continues_after_failure(self):
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
        failing = MagicMock()
        failing.item = DockItem(desktop_id="applet://bad", name="Bad")
        failing.start.side_effect = RuntimeError("boom")
        working = MagicMock()
        working.item = DockItem(desktop_id="applet://good", name="Good")
        model._applets["applet://bad"] = failing
        model._applets["applet://good"] = working

        model.start_applets()

        failing.start.assert_called_once()
        working.start.assert_called_once()

    def test_stop_applets_continues_after_failure(self):
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
        failing = MagicMock()
        failing.item = DockItem(desktop_id="applet://bad", name="Bad")
        failing.stop.side_effect = RuntimeError("boom")
        working = MagicMock()
        working.item = DockItem(desktop_id="applet://good", name="Good")
        model._applets["applet://bad"] = failing
        model._applets["applet://good"] = working

        model.stop_applets()

        failing.stop.assert_called_once()
        working.stop.assert_called_once()

    def test_add_applet_start_failure_rolls_back_without_persisting(self, monkeypatch):
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
        fake_item = DockItem(desktop_id="applet://session", name="Session")
        fake_applet = MagicMock()
        fake_applet.item = fake_item
        fake_applet.start.side_effect = RuntimeError("boom")

        class FakeAppletClass:
            def __new__(cls, icon_size, config):
                return fake_applet

        import docking.applets as applets_mod

        monkeypatch.setattr(
            applets_mod,
            "get_applet_catalog",
            lambda: {"session": object()},
        )
        monkeypatch.setattr(
            applets_mod,
            "load_applet_class",
            lambda applet_id: FakeAppletClass if applet_id == "session" else None,
        )

        model.add_applet("session")

        fake_applet.start.assert_called_once()
        fake_applet.stop.assert_called_once()
        assert model.get_applet("applet://session") is None
        assert model.pinned_items == []
        config.save.assert_not_called()

    def test_add_separator_start_failure_rolls_back_without_persisting(
        self, monkeypatch
    ):
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
        fake_item = DockItem(desktop_id="applet://separator", name="Separator")
        fake_applet = MagicMock()
        fake_applet.item = fake_item
        fake_applet.start.side_effect = RuntimeError("boom")

        class FakeSeparatorClass:
            def __new__(cls, icon_size, config):
                return fake_applet

        import docking.applets as applets_mod

        monkeypatch.setattr(
            applets_mod,
            "load_applet_class",
            lambda applet_id: FakeSeparatorClass if applet_id == "separator" else None,
        )

        model.add_separator(index=0)

        fake_applet.start.assert_called_once()
        fake_applet.stop.assert_called_once()
        assert model.pinned_items == []
        assert model._applets == {}
        config.save.assert_not_called()

    def test_remove_applet_continues_when_stop_fails(self):
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
        callback = MagicMock()
        model.add_change_listener(callback)
        applet = MagicMock()
        applet.item = DockItem(desktop_id="applet://bad", name="Bad")
        applet.stop.side_effect = RuntimeError("boom")
        model._applets["applet://bad"] = applet
        model.pinned_items.append(applet.item)

        model.remove_applet("applet://bad")

        applet.stop.assert_called_once()
        assert model.get_applet("applet://bad") is None
        assert applet.item not in model.pinned_items
        config.save.assert_called_once()
        callback.assert_called_once()

    def test_start_applets_passes_notify_callback(self):
        # Given
        config = _make_config([])
        launcher = _make_launcher()
        model = DockModel(config, launcher, AppletServices())
        callback = MagicMock()
        model.add_change_listener(callback)
        applet_callback = MagicMock()
        model.add_applet_change_listener(applet_callback)

        class StrictApplet:
            def __init__(self):
                self.item = DockItem(desktop_id="applet://strict", name="Strict")
                self.start_calls = 0
                self.stop_calls = 0

            def start(self, notify):
                self.start_calls += 1
                notify()

            def stop(self):
                self.stop_calls += 1

        applet = StrictApplet()
        model._applets["applet://strict"] = applet

        # When
        model.start_applets()

        # Then
        assert applet.start_calls == 1
        callback.assert_called_once()
        applet_callback.assert_called_once_with("applet://strict")

    def test_find_by_desktop_id_and_unpin_applet_route(self, monkeypatch):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
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


class TestLauncherEntryState:
    def test_apply_launcher_entry_updates_existing_item(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())

        applied = model.apply_launcher_entry(
            sender_name=":1.7",
            app_uri="application://a.desktop",
            state=LauncherEntryState(
                sender_name=":1.7",
                app_uri="application://a.desktop",
                desktop_id="a.desktop",
                badge_count=4,
                badge_visible=True,
                progress=0.6,
                progress_visible=True,
                urgent=True,
            ),
        )

        item = model.visible_items()[0]
        assert applied is True
        assert item.badge_count == 4
        assert item.badge_visible is True
        assert item.progress == 0.6
        assert item.progress_visible is True
        assert item.launcher_entry_urgent is True
        assert item.is_urgent is True

    def test_hidden_badge_retains_count_on_existing_item(self):
        config = _make_config(["a.desktop"])
        config.show_launcher_badges = False
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())

        model.apply_launcher_entry(
            sender_name=":1.7",
            app_uri="application://a.desktop",
            state=LauncherEntryState(
                sender_name=":1.7",
                app_uri="application://a.desktop",
                desktop_id="a.desktop",
                badge_count=4,
                badge_visible=True,
            ),
        )

        item = model.visible_items()[0]
        assert item.badge_count == 4
        assert item.badge_visible is False

    def test_hidden_progress_retains_value_on_existing_item(self):
        config = _make_config(["a.desktop"])
        config.show_launcher_progress = False
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())

        model.apply_launcher_entry(
            sender_name=":1.7",
            app_uri="application://a.desktop",
            state=LauncherEntryState(
                sender_name=":1.7",
                app_uri="application://a.desktop",
                desktop_id="a.desktop",
                progress=0.6,
                progress_visible=True,
            ),
        )

        item = model.visible_items()[0]
        assert item.progress == 0.6
        assert item.progress_visible is False

    def test_hidden_overlay_does_not_create_launcher_only_transient(self):
        config = _make_config([])
        config.show_launcher_badges = False
        config.show_launcher_progress = False
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            badge_count=7,
            badge_visible=True,
            progress=0.5,
            progress_visible=True,
        )

        applied = model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        assert applied is False
        assert model.visible_items() == []

        config.show_launcher_badges = True
        model.refresh_launcher_overlay_visibility()

        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "mail.desktop"
        assert items[0].badge_count == 7
        assert items[0].badge_visible is True
        assert items[0].progress == 0.5
        assert items[0].progress_visible is False

    def test_zero_count_badge_does_not_create_launcher_only_transient(self):
        config = _make_config([])
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            badge_count=0,
            badge_visible=True,
        )

        applied = model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        assert applied is False
        assert model.visible_items() == []

    def test_refresh_hides_transient_and_reenabling_restores_cached_state(self):
        config = _make_config([])
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            badge_count=7,
            badge_visible=True,
        )
        model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        config.show_launcher_badges = False
        model.refresh_launcher_overlay_visibility()

        assert model.visible_items() == []

        config.show_launcher_badges = True
        model.refresh_launcher_overlay_visibility()

        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "mail.desktop"
        assert items[0].badge_count == 7
        assert items[0].badge_visible is True

    def test_refresh_keeps_urgent_transient_when_visual_overlays_are_hidden(self):
        config = _make_config([])
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            badge_count=7,
            badge_visible=True,
            urgent=True,
        )
        model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        config.show_launcher_badges = False
        config.show_launcher_progress = False
        model.refresh_launcher_overlay_visibility()

        items = model.visible_items()
        assert len(items) == 1
        assert items[0].badge_visible is False
        assert items[0].launcher_entry_urgent is True
        assert items[0].is_urgent is True

    def test_refresh_preserves_latest_sender_urgency(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        urgent_state = LauncherEntryState(
            sender_name=":1.7",
            app_uri="application://a.desktop",
            desktop_id="a.desktop",
            urgent=True,
        )
        badge_state = LauncherEntryState(
            sender_name=":1.8",
            app_uri="application://a.desktop",
            desktop_id="a.desktop",
            badge_count=4,
            badge_visible=True,
        )
        model.apply_launcher_entry(
            sender_name=urgent_state.sender_name,
            app_uri=urgent_state.app_uri,
            state=urgent_state,
        )
        model.apply_launcher_entry(
            sender_name=badge_state.sender_name,
            app_uri=badge_state.app_uri,
            state=badge_state,
        )
        model.apply_launcher_entry(
            sender_name=urgent_state.sender_name,
            app_uri=urgent_state.app_uri,
            state=urgent_state,
        )
        assert model.visible_items()[0].launcher_entry_urgent is True

        config.show_launcher_badges = False
        model.refresh_launcher_overlay_visibility()

        item = model.visible_items()[0]
        assert item.badge_visible is False
        assert item.launcher_entry_urgent is True
        assert item.is_urgent is True

    def test_launcher_entry_urgent_survives_running_rescan(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.apply_launcher_entry(
            sender_name=":1.7",
            app_uri="application://a.desktop",
            state=LauncherEntryState(
                sender_name=":1.7",
                app_uri="application://a.desktop",
                desktop_id="a.desktop",
                urgent=True,
            ),
        )

        model.update_running({"a.desktop": _running(urgent=False)})

        item = model.visible_items()[0]
        assert item.window_urgent is False
        assert item.launcher_entry_urgent is True
        assert item.is_urgent is True

    def test_apply_launcher_entry_creates_transient_after_retry_phase(self):
        config = _make_config([])
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            badge_count=7,
            badge_visible=True,
        )

        first = model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
        )
        second = model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        assert first is False
        assert second is True
        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "mail.desktop"
        assert items[0].is_pinned is False
        assert items[0].is_running is False
        assert items[0].badge_count == 7

    def test_apply_launcher_entry_creates_urgent_only_transient(self):
        config = _make_config([])
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            urgent=True,
        )

        applied = model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        assert applied is True
        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "mail.desktop"
        assert items[0].is_running is False
        assert items[0].is_urgent is True
        assert items[0].launcher_entry_urgent is True

    def test_remove_launcher_entry_drops_launcher_only_transient(self):
        config = _make_config([])
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            badge_count=7,
            badge_visible=True,
        )
        model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        model.remove_launcher_entry(sender_name=":1.9")

        assert model.visible_items() == []

    def test_update_running_preserves_launcher_only_transient(self):
        config = _make_config([])
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            badge_count=3,
            badge_visible=True,
        )
        model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        model.update_running({})

        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "mail.desktop"
        assert items[0].badge_count == 3
        assert items[0].is_running is False

    def test_update_running_preserves_urgent_only_launcher_transient(self):
        config = _make_config([])
        launcher = _make_launcher("mail.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.9",
            app_uri="application://mail.desktop",
            desktop_id="mail.desktop",
            urgent=True,
        )
        model.apply_launcher_entry(
            sender_name=":1.9",
            app_uri=state.app_uri,
            state=state,
            create_transient=True,
        )

        model.update_running({})

        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "mail.desktop"
        assert items[0].is_running is False
        assert items[0].is_urgent is True

    def test_unpin_with_launcher_overlay_becomes_transient(self):
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        state = LauncherEntryState(
            sender_name=":1.7",
            app_uri="application://a.desktop",
            desktop_id="a.desktop",
            badge_count=2,
            badge_visible=True,
        )
        model.apply_launcher_entry(
            sender_name=":1.7",
            app_uri=state.app_uri,
            state=state,
        )

        model.unpin_item("a.desktop")

        items = model.visible_items()
        assert len(items) == 1
        assert items[0].desktop_id == "a.desktop"
        assert items[0].is_pinned is False
        assert items[0].badge_count == 2


class TestStatusNotifierOverlayState:
    def test_applies_badge_and_urgency_to_pinned_item(self, monkeypatch):
        config = _make_config(["slack.desktop"])
        launcher = _make_launcher("slack.desktop")
        model = DockModel(config, launcher, AppletServices())
        monkeypatch.setattr(
            "docking.platform.model.GLib.get_monotonic_time",
            lambda: 100,
        )

        applied = model.apply_status_notifier_overlay(
            source_id=":1.20/StatusNotifierItem",
            desktop_id="slack.desktop",
            badge_count=3,
        )

        item = model.visible_items()[0]
        assert applied is True
        assert item.badge_count == 3
        assert item.badge_visible is True
        assert item.status_notifier_badge_count == 3
        assert item.status_notifier_urgent is True
        assert item.is_urgent is True
        assert item.last_urgent == 100

    def test_count_increase_retriggers_urgent_timestamp(self, monkeypatch):
        config = _make_config(["slack.desktop"])
        launcher = _make_launcher("slack.desktop")
        model = DockModel(config, launcher, AppletServices())
        timestamps = iter((100, 200))
        monkeypatch.setattr(
            "docking.platform.model.GLib.get_monotonic_time",
            lambda: next(timestamps),
        )

        model.apply_status_notifier_overlay(
            source_id="slack-item",
            desktop_id="slack.desktop",
            badge_count=1,
        )
        item = model.visible_items()[0]
        assert item.last_urgent == 100

        model.apply_status_notifier_overlay(
            source_id="slack-item",
            desktop_id="slack.desktop",
            badge_count=1,
        )
        assert item.last_urgent == 100

        model.apply_status_notifier_overlay(
            source_id="slack-item",
            desktop_id="slack.desktop",
            badge_count=2,
        )
        assert item.last_urgent == 200

    def test_zero_count_clears_badge_and_status_notifier_urgency(self):
        config = _make_config(["slack.desktop"])
        launcher = _make_launcher("slack.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.apply_status_notifier_overlay(
            source_id="slack-item",
            desktop_id="slack.desktop",
            badge_count=2,
        )

        model.apply_status_notifier_overlay(
            source_id="slack-item",
            desktop_id="slack.desktop",
            badge_count=0,
        )

        item = model.visible_items()[0]
        assert item.badge_count == 0
        assert item.badge_visible is False
        assert item.status_notifier_urgent is False
        assert item.is_urgent is False

    def test_unity_and_status_notifier_badges_do_not_overwrite_each_other(self):
        config = _make_config(["slack.desktop"])
        launcher = _make_launcher("slack.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.apply_status_notifier_overlay(
            source_id="slack-item",
            desktop_id="slack.desktop",
            badge_count=5,
        )
        model.apply_launcher_entry(
            sender_name=":1.7",
            app_uri="application://slack.desktop",
            state=LauncherEntryState(
                sender_name=":1.7",
                app_uri="application://slack.desktop",
                desktop_id="slack.desktop",
                badge_count=2,
                badge_visible=True,
            ),
        )

        item = model.visible_items()[0]
        assert item.launcher_entry_badge_count == 2
        assert item.status_notifier_badge_count == 5
        assert item.badge_count == 5

        model.remove_launcher_entry(sender_name=":1.7")
        assert item.badge_count == 5
        assert item.badge_visible is True

        model.remove_status_notifier_overlay(source_id="slack-item")
        assert item.badge_count == 0
        assert item.badge_visible is False

    def test_multiple_tray_sources_use_highest_count_and_fall_back(self):
        config = _make_config(["slack.desktop"])
        launcher = _make_launcher("slack.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.apply_status_notifier_overlay(
            source_id="slack-one",
            desktop_id="slack.desktop",
            badge_count=2,
        )
        model.apply_status_notifier_overlay(
            source_id="slack-two",
            desktop_id="slack.desktop",
            badge_count=7,
        )

        item = model.visible_items()[0]
        assert item.badge_count == 7

        assert model.remove_status_notifier_overlay(source_id="slack-two")
        assert item.badge_count == 2
        assert item.is_urgent is True

        assert model.remove_status_notifier_overlay(source_id="slack-one")
        assert item.badge_count == 0
        assert item.is_urgent is False

    def test_badge_preference_hides_but_retains_status_notifier_count(self):
        config = _make_config(["slack.desktop"])
        config.show_launcher_badges = False
        launcher = _make_launcher("slack.desktop")
        model = DockModel(config, launcher, AppletServices())

        model.apply_status_notifier_overlay(
            source_id="slack-item",
            desktop_id="slack.desktop",
            badge_count=4,
        )

        item = model.visible_items()[0]
        assert item.badge_count == 4
        assert item.badge_visible is False
        assert item.status_notifier_urgent is True


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
        model = DockModel(config, launcher, AppletServices())
        # When
        model.update_running({"a.desktop": _running(urgent=True)})
        # Then
        item = model.visible_items()[0]
        assert item.is_urgent is True
        assert item.last_urgent != 0  # timestamp was set

    def test_urgent_timestamp_set_only_on_transition(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        # When
        model.update_running({"a.desktop": _running(urgent=True)})
        first_ts = model.visible_items()[0].last_urgent
        # When
        model.update_running({"a.desktop": _running(urgent=True)})
        second_ts = model.visible_items()[0].last_urgent
        # Then
        assert second_ts is first_ts

    def test_urgent_clears(self):
        # Given
        config = _make_config(["a.desktop"])
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.update_running({"a.desktop": _running(urgent=True)})
        # When
        model.update_running({"a.desktop": _running(urgent=False)})
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


class TestRecentAppsIntegration:
    """Integration tests for the recent apps section of DockModel."""

    def test_recent_apps_disabled_no_section_appears(self):
        config = _make_config(["a.desktop"], show_recent_apps=False)
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        # No recent apps in visible items
        items = model.visible_items()
        assert all(not item.is_recent for item in items)

    def test_recent_apps_enabled_empty_by_default(self):
        config = _make_config(["a.desktop"], show_recent_apps=True)
        launcher = _make_launcher("a.desktop")
        model = DockModel(config, launcher, AppletServices())
        # Only pinned items, no recent section because no apps closed
        items = model.visible_items()
        assert all(not item.is_recent for item in items)

    def test_app_closed_appears_in_recent(self):
        config = _make_config(["a.desktop"], show_recent_apps=True)
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        # App b is running
        model.update_running({"b.desktop": _running()})
        items_before = model.visible_items()
        b_before = next(i for i in items_before if i.desktop_id == "b.desktop")
        assert b_before.is_running
        assert not b_before.is_recent
        # App b closes
        model.update_running({})
        items_after = model.visible_items()
        # b.desktop should now be marked as recent
        b_items = [
            i for i in items_after if i.desktop_id == "b.desktop" and i.is_recent
        ]
        assert len(b_items) >= 1

    def test_recent_app_not_duplicated_when_pinned(self):
        config = _make_config(["firefox.desktop"], show_recent_apps=True)
        launcher = _make_launcher("firefox.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.update_running({"firefox.desktop": _running()})
        model.update_running({})
        items = model.visible_items()
        # Firefox is pinned, should not appear as recent
        recent_ids = [i.desktop_id for i in items if i.is_recent]
        assert "firefox.desktop" not in recent_ids

    def test_pin_recent_item_moves_it_to_pinned(self):
        config = _make_config(["a.desktop"], show_recent_apps=True)
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        model.update_running({"b.desktop": _running()})
        model.update_running({})
        # b.desktop should be in recent
        items_before = model.visible_items()
        recent_before = [i for i in items_before if i.is_recent]
        assert any(i.desktop_id == "b.desktop" for i in recent_before)
        # Pin it
        model.pin_item("b.desktop")
        # Should now be pinned
        items = model.visible_items()
        pinned_ids = [i.desktop_id for i in items if i.is_pinned]
        assert "b.desktop" in pinned_ids

    def test_recent_app_max_limit(self):
        config = _make_config(["pinned.desktop"], show_recent_apps=True)
        config.recent_apps_max = 2
        launcher = _make_launcher(
            "pinned.desktop", "a.desktop", "b.desktop", "c.desktop"
        )
        model = DockModel(config, launcher, AppletServices())
        # Close three apps
        for did in ["a.desktop", "b.desktop", "c.desktop"]:
            model.update_running({did: _running()})
        model.update_running({})
        recent = [i for i in model.visible_items() if i.is_recent]
        assert len(recent) <= 2

    def test_recent_app_running_not_in_recent_section(self):
        config = _make_config(["a.desktop"], show_recent_apps=True)
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        # b closes
        model.update_running({"b.desktop": _running()})
        model.update_running({})
        # b should be recent
        assert any(
            i.desktop_id == "b.desktop" and i.is_recent for i in model.visible_items()
        )
        # b starts again
        model.update_running({"b.desktop": _running()})
        # b should now be transient (running but not pinned)
        items = model.visible_items()
        b_item = next(i for i in items if i.desktop_id == "b.desktop")
        assert not b_item.is_recent
        assert b_item.is_running

    def test_rebuild_recent_apps_public_method(self):
        config = _make_config(["a.desktop"], show_recent_apps=True)
        config.recent_apps = [{"desktop_id": "b.desktop", "last_closed": 9999999999}]
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        # Clear via rebuild
        config.show_recent_apps = False
        model.rebuild_recent_apps()
        assert all(not i.is_recent for i in model.visible_items())
        # Re-enable
        config.show_recent_apps = True
        config.recent_apps = [{"desktop_id": "b.desktop", "last_closed": 9999999999}]
        model.rebuild_recent_apps()
        recent = [i for i in model.visible_items() if i.is_recent]
        # b.desktop should be in recent (from config list)
        assert any(i.desktop_id == "b.desktop" for i in recent)

    def test_find_by_desktop_id_searches_recent(self):
        config = _make_config(["a.desktop"], show_recent_apps=True)
        config.recent_apps = [{"desktop_id": "b.desktop", "last_closed": 9999999999}]
        launcher = _make_launcher("a.desktop", "b.desktop")
        model = DockModel(config, launcher, AppletServices())
        # Find recent item
        item = model.find_by_desktop_id("b.desktop")
        assert item is not None
        assert item.is_recent

    def test_recent_app_retention_days_pruning(self):
        import time

        config = _make_config(["a.desktop"], show_recent_apps=True)
        config.recent_apps_retention_days = 1
        config.recent_apps_max = 10
        launcher = _make_launcher("a.desktop", "old.desktop", "new.desktop")
        model = DockModel(config, launcher, AppletServices())
        # Close an app with old timestamp
        model.update_running({"old.desktop": _running()})
        model.update_running({})
        # Manually set the last_closed to be old
        for item in model.visible_items():
            if item.desktop_id == "old.desktop" and item.is_recent:
                item.last_closed = time.time() - (2 * 86400)  # 2 days ago
        # Now close a new app
        model.update_running({"new.desktop": _running()})
        model.update_running({})
        recent = [i for i in model.visible_items() if i.is_recent]
        # old.desktop should be pruned (older than 1 day), new.desktop should remain
        recent_ids = [i.desktop_id for i in recent]
        assert "new.desktop" in recent_ids
