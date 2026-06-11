"""Tests for the trash applet."""

import configparser
import contextlib
import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

from docking.applets.trash import applet as trash_applet_mod
from docking.applets.trash.applet import TrashApplet
from docking.applets.trash.backend import (
    CinnamonTrashBackend,
    GioTrashBackend,
    GnomeTrashBackend,
    KdeTrashBackend,
    MateTrashBackend,
    _empty_host_trash,
    _host_user_data_home,
    _kde_kiorc_file,
    _open_trash_uri,
    _visible_trash_files_directory,
    select_trash_backend,
)
from docking.platform.environment import Desktop


class TestHelperFunctions:
    def test_host_user_data_home(self):
        assert _host_user_data_home() == Path.home() / ".local" / "share"

    def test_visible_trash_files_directory_not_flatpak(self, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", "/tmp/test-xdg/data")
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: False)
        result = _visible_trash_files_directory()
        assert result == Path("/tmp/test-xdg/data") / "Trash" / "files"

    def test_kde_kiorc_file_not_flatpak(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg/config")
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: False)
        result = _kde_kiorc_file()
        assert result == Path("/tmp/test-xdg/config") / "kiorc"

    def test_open_trash_uri_handles_glib_error(self):
        from gi.repository import GLib

        with patch(
            "docking.applets.trash.backend.Gio.AppInfo.launch_default_for_uri",
            side_effect=GLib.Error("no handler"),
        ):
            _open_trash_uri("trash:///")

    def test_empty_host_trash_no_command_returns_false(self):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.host_command",
                return_value=None,
            ),
        ):
            assert _empty_host_trash() is False

    def test_empty_host_trash_oserror_returns_false(self):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.host_command",
                return_value=["flatpak-spawn", "--host", "gio", "trash", "--empty"],
            ),
            patch(
                "docking.applets.trash.backend.subprocess.run",
                side_effect=OSError("spawn failed"),
            ),
        ):
            assert _empty_host_trash() is False

    def test_empty_host_trash_timeout_returns_false(self):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.host_command",
                return_value=["flatpak-spawn", "--host", "gio", "trash", "--empty"],
            ),
            patch(
                "docking.applets.trash.backend.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd=["gio"], timeout=10.0),
            ),
        ):
            assert _empty_host_trash() is False

    def test_empty_host_trash_non_zero_returncode_returns_false(self):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.host_command",
                return_value=["flatpak-spawn", "--host", "gio", "trash", "--empty"],
            ),
            patch(
                "docking.applets.trash.backend.subprocess.run",
            ) as run_mock,
        ):
            run_mock.return_value.returncode = 1
            run_mock.return_value.stderr = "failed"
            run_mock.return_value.stdout = ""
            assert _empty_host_trash() is False


class TestTrashBackendSelection:
    def test_selects_desktop_specific_backends(self):
        assert isinstance(select_trash_backend(desktop=Desktop.KDE), KdeTrashBackend)
        assert isinstance(select_trash_backend(desktop=Desktop.MATE), MateTrashBackend)
        assert isinstance(
            select_trash_backend(desktop=Desktop.CINNAMON), CinnamonTrashBackend
        )
        assert isinstance(
            select_trash_backend(desktop=Desktop.GNOME), GnomeTrashBackend
        )
        assert isinstance(
            select_trash_backend(desktop=Desktop.UNKNOWN), GioTrashBackend
        )


class TestGioTrashBackend:
    def test_counts_items(self):
        # Given an enumerator yielding 3 items
        mock_enum = MagicMock()
        mock_enum.next_file.side_effect = [MagicMock(), MagicMock(), MagicMock(), None]
        mock_file = MagicMock()
        mock_file.enumerate_children.return_value = mock_enum

        # When
        with patch(
            "docking.applets.trash.backend.Gio.File.new_for_uri", return_value=mock_file
        ):
            count = GioTrashBackend().count_items()

        # Then
        assert count == 3

    def test_returns_zero_on_count_error(self):
        # Given enumerate_children raises
        from gi.repository import GLib

        mock_file = MagicMock()
        mock_file.enumerate_children.side_effect = GLib.Error("fail")

        with patch(
            "docking.applets.trash.backend.Gio.File.new_for_uri", return_value=mock_file
        ):
            assert GioTrashBackend().count_items() == 0

    def test_counts_visible_trash_files_in_flatpak(self, tmp_path):
        files_dir = tmp_path / "Trash" / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "a.txt").write_text("a")
        (files_dir / "b.txt").write_text("b")

        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend._visible_trash_files_directory",
                return_value=files_dir,
            ),
            patch("docking.applets.trash.backend.Gio.File.new_for_uri") as new_for_uri,
        ):
            assert GioTrashBackend().count_items() == 2

        new_for_uri.assert_not_called()

    def test_monitor_uses_visible_trash_files_in_flatpak(self, tmp_path):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend._visible_trash_files_directory",
                return_value=tmp_path,
            ),
            patch(
                "docking.applets.trash.backend.Gio.File.new_for_path"
            ) as new_for_path,
            patch("docking.applets.trash.backend.Gio.File.new_for_uri") as new_for_uri,
        ):
            GioTrashBackend().monitor_file()

        new_for_path.assert_called_once_with(str(tmp_path))
        new_for_uri.assert_not_called()

    def test_open_launches_default_trash_uri(self):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=False),
            patch(
                "docking.applets.trash.backend.Gio.AppInfo.launch_default_for_uri"
            ) as launch,
        ):
            GioTrashBackend().open()

        launch.assert_called_once_with("trash:///", None)

    def test_open_prefers_sanitized_host_gio_in_flatpak(self):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.spawn_path",
                return_value="/usr/bin/flatpak-spawn",
            ),
            patch("docking.applets.trash.backend.subprocess.Popen") as popen,
            patch(
                "docking.applets.trash.backend.Gio.AppInfo.launch_default_for_uri"
            ) as launch,
        ):
            GioTrashBackend().open()

        popen.assert_called_once_with(
            [
                "/usr/bin/flatpak-spawn",
                "--host",
                "env",
                "-u",
                "GIO_USE_VFS",
                "-u",
                "GI_TYPELIB_PATH",
                "-u",
                "GSETTINGS_SCHEMA_DIR",
                "-u",
                "XDG_DATA_DIRS",
                "gio",
                "open",
                "trash:///",
            ]
        )
        launch.assert_not_called()

    def test_open_falls_back_to_gio_when_host_open_unavailable(self):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.spawn_path",
                return_value="/usr/bin/flatpak-spawn",
            ),
            patch(
                "docking.applets.trash.backend.subprocess.Popen",
                side_effect=OSError("spawn failed"),
            ) as popen,
            patch(
                "docking.applets.trash.backend.Gio.AppInfo.launch_default_for_uri",
            ) as launch,
        ):
            GioTrashBackend().open()

        popen.assert_called_once_with(
            [
                "/usr/bin/flatpak-spawn",
                "--host",
                "env",
                "-u",
                "GIO_USE_VFS",
                "-u",
                "GI_TYPELIB_PATH",
                "-u",
                "GSETTINGS_SCHEMA_DIR",
                "-u",
                "XDG_DATA_DIRS",
                "gio",
                "open",
                "trash:///",
            ]
        )
        launch.assert_called_once_with("trash:///", None)

    def test_empty_trash_uses_dbus_first(self):
        bus = MagicMock()
        with patch("docking.applets.trash.backend.Gio.bus_get_sync", return_value=bus):
            GioTrashBackend().empty(lambda: False)

        bus.call_sync.assert_called_once()

    def test_empty_trash_bypasses_dbus_when_file_manager_disables_confirmation(self):
        backend = MateTrashBackend()
        with (
            patch.object(backend, "_confirmation_preference", return_value=False),
            patch("docking.applets.trash.backend.Gio.bus_get_sync") as bus_get,
            patch.object(backend, "_delete_contents") as delete_mock,
        ):
            backend.empty(lambda: False)

        bus_get.assert_not_called()
        delete_mock.assert_called_once()

    def test_empty_trash_falls_back_to_delete(self):
        from gi.repository import GLib

        backend = GnomeTrashBackend()
        bus = MagicMock()
        bus.call_sync.side_effect = GLib.Error("nautilus")
        with (
            patch("docking.applets.trash.backend.Gio.bus_get_sync", return_value=bus),
            patch.object(backend, "_delete_contents") as delete_mock,
        ):
            backend.empty(lambda: False)

        delete_mock.assert_called_once()

    def test_empty_trash_uses_host_gio_in_flatpak_after_dbus_failure(self):
        from gi.repository import GLib

        backend = GnomeTrashBackend()
        bus = MagicMock()
        bus.call_sync.side_effect = GLib.Error("nautilus")
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.spawn_path",
                return_value="/usr/bin/flatpak-spawn",
            ),
            patch("docking.applets.trash.backend.Gio.bus_get_sync", return_value=bus),
            patch("docking.applets.trash.backend.subprocess.run") as run_mock,
            patch.object(backend, "_delete_contents") as delete_mock,
        ):
            run_mock.return_value.returncode = 0
            backend.empty(lambda: False)

        run_mock.assert_called_once_with(
            [
                "/usr/bin/flatpak-spawn",
                "--host",
                "env",
                "-u",
                "GIO_USE_VFS",
                "-u",
                "GI_TYPELIB_PATH",
                "-u",
                "GSETTINGS_SCHEMA_DIR",
                "-u",
                "XDG_DATA_DIRS",
                "gio",
                "trash",
                "--empty",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        delete_mock.assert_not_called()

    def test_empty_trash_falls_back_to_delete_when_host_gio_fails(self):
        from gi.repository import GLib

        backend = GnomeTrashBackend()
        bus = MagicMock()
        bus.call_sync.side_effect = GLib.Error("nautilus")
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.spawn_path",
                return_value="/usr/bin/flatpak-spawn",
            ),
            patch("docking.applets.trash.backend.Gio.bus_get_sync", return_value=bus),
            patch("docking.applets.trash.backend.subprocess.run") as run_mock,
            patch.object(backend, "_delete_contents") as delete_mock,
        ):
            run_mock.return_value.returncode = 1
            run_mock.return_value.stderr = "nope"
            run_mock.return_value.stdout = ""
            backend.empty(lambda: False)

        delete_mock.assert_called_once()

    def test_mate_confirmation_preference_uses_caja_schema(self):
        assert MateTrashBackend.confirmation_schema == (
            "org.mate.caja.preferences",
            "/org/mate/caja/preferences/",
        )

    def test_cinnamon_confirmation_preference_uses_nemo_schema(self):
        assert CinnamonTrashBackend.confirmation_schema == (
            "org.nemo.preferences",
            "/org/nemo/preferences/",
        )

    def test_delete_trash_contents_deletes_children(self):
        info_a = MagicMock()
        info_a.get_name.return_value = "a.txt"
        info_b = MagicMock()
        info_b.get_name.return_value = "b.txt"
        enumerator = MagicMock()
        enumerator.next_file.side_effect = [info_a, info_b, None]
        child_a = MagicMock()
        child_b = MagicMock()
        trash = MagicMock()
        trash.enumerate_children.return_value = enumerator
        trash.get_child.side_effect = [child_a, child_b]

        with patch(
            "docking.applets.trash.backend.Gio.File.new_for_uri", return_value=trash
        ):
            GioTrashBackend()._delete_contents()

        child_a.delete.assert_called_once()
        child_b.delete.assert_called_once()
        enumerator.close.assert_called_once()

    def test_delete_contents_handles_enumerate_error(self):
        from gi.repository import GLib

        trash = MagicMock()
        trash.enumerate_children.side_effect = GLib.Error("enumerate fail")

        with patch(
            "docking.applets.trash.backend.Gio.File.new_for_uri", return_value=trash
        ):
            GioTrashBackend()._delete_contents()

    def test_delete_contents_child_error(self):
        from gi.repository import GLib

        info = MagicMock()
        info.get_name.return_value = "a.txt"
        enumerator = MagicMock()
        enumerator.next_file.side_effect = [info, None]
        child = MagicMock()
        child.delete.side_effect = GLib.Error("delete fail")
        trash = MagicMock()
        trash.enumerate_children.return_value = enumerator
        trash.get_child.return_value = child

        with patch(
            "docking.applets.trash.backend.Gio.File.new_for_uri", return_value=trash
        ):
            GioTrashBackend()._delete_contents()

        enumerator.close.assert_called_once()

    def test_count_items_handles_flatpak_oserror(self, monkeypatch):

        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: True)
        monkeypatch.setattr(
            "docking.applets.trash.backend._visible_trash_files_directory",
            lambda: Path("/nonexistent/path"),
        )
        assert GioTrashBackend().count_items() == 0

    def test_confirmation_preference_no_schema(self):
        backend = GioTrashBackend()
        assert backend._confirmation_preference() is None

    def test_confirmation_preference_default_source_is_none(self, monkeypatch):
        from gi.repository import Gio

        monkeypatch.setattr(
            Gio.SettingsSchemaSource,
            "get_default",
            lambda: None,
        )
        backend = MateTrashBackend()
        assert backend._confirmation_preference() is None

    def test_confirmation_preference_schema_not_found(self, monkeypatch):
        from gi.repository import Gio

        source = MagicMock()
        source.lookup.return_value = None
        monkeypatch.setattr(
            Gio.SettingsSchemaSource,
            "get_default",
            lambda: source,
        )
        backend = MateTrashBackend()
        assert backend._confirmation_preference() is None

    def test_confirmation_preference_key_not_found(self, monkeypatch):
        from gi.repository import Gio

        schema = MagicMock()
        schema.has_key.return_value = False
        source = MagicMock()
        source.lookup.return_value = schema
        monkeypatch.setattr(
            Gio.SettingsSchemaSource,
            "get_default",
            lambda: source,
        )
        backend = MateTrashBackend()
        assert backend._confirmation_preference() is None

    def test_confirmation_preference_reads_bool(self, monkeypatch):
        from gi.repository import Gio

        settings = MagicMock()
        settings.get_boolean.return_value = True
        schema = MagicMock()
        schema.has_key.return_value = True
        source = MagicMock()
        source.lookup.return_value = schema
        monkeypatch.setattr(
            Gio.SettingsSchemaSource,
            "get_default",
            lambda: source,
        )
        monkeypatch.setattr(
            Gio.Settings,
            "new_full",
            lambda schema_obj, backend_path, path: settings,
        )
        backend = MateTrashBackend()
        assert backend._confirmation_preference() is True

    def test_confirmation_preference_handles_exception(self, monkeypatch):
        from gi.repository import Gio

        settings = MagicMock()
        settings.get_boolean.side_effect = Exception("settings unavailable")
        schema = MagicMock()
        schema.has_key.return_value = True
        source = MagicMock()
        source.lookup.return_value = schema
        monkeypatch.setattr(
            Gio.SettingsSchemaSource,
            "get_default",
            lambda: source,
        )
        monkeypatch.setattr(
            Gio.Settings,
            "new_full",
            lambda schema_obj, backend_path, path: settings,
        )
        backend = MateTrashBackend()
        assert backend._confirmation_preference() is None

    def test_empty_via_dbus_bus_error(self, monkeypatch):
        from gi.repository import GLib

        monkeypatch.setattr(
            "docking.applets.trash.backend.Gio.bus_get_sync",
            lambda *a, **kw: (_ for _ in ()).throw(GLib.Error("no bus")),
        )
        assert GioTrashBackend()._empty_via_dbus() is False


class TestKdeTrashBackend:
    def test_uses_kde_trash_uri(self):
        assert KdeTrashBackend().uri == "trash:/"
        assert GioTrashBackend().uri == "trash:///"

    def test_counts_kde_trash_files(self, tmp_path, monkeypatch):
        files_dir = tmp_path / "Trash" / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "a.txt").write_text("a")
        (files_dir / "b.txt").write_text("b")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        assert KdeTrashBackend().count_items() == 2

    def test_deletes_kde_trash_files_and_info(self, tmp_path, monkeypatch):
        files_dir = tmp_path / "Trash" / "files"
        info_dir = tmp_path / "Trash" / "info"
        files_dir.mkdir(parents=True)
        info_dir.mkdir()
        (files_dir / "a.txt").write_text("a")
        nested_dir = files_dir / "nested"
        nested_dir.mkdir()
        (nested_dir / "b.txt").write_text("b")
        (info_dir / "a.txt.trashinfo").write_text("[Trash Info]")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        KdeTrashBackend().empty(lambda: True)

        assert list(files_dir.iterdir()) == []
        assert list(info_dir.iterdir()) == []

    def test_open_uses_kde_open_command(self):
        backend = KdeTrashBackend()
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=False),
            patch.object(
                backend, "_available_open_command", return_value=("dolphin", "trash:/")
            ),
            patch("docking.applets.trash.backend.subprocess.Popen") as popen,
        ):
            backend.open()

        popen.assert_called_once_with(("dolphin", "trash:/"))

    def test_monitor_uses_kde_trash_files(self, tmp_path):
        with (
            patch(
                "docking.applets.trash.backend._kde_trash_files_directory",
                return_value=tmp_path,
            ),
            patch(
                "docking.applets.trash.backend.Gio.File.new_for_path",
            ) as new_for_path,
        ):
            KdeTrashBackend().monitor_file()

        new_for_path.assert_called_once_with(str(tmp_path))

    def test_kde_count_uses_visible_host_trash_files_in_flatpak(
        self, tmp_path, monkeypatch
    ):
        files_dir = tmp_path / "Trash" / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "a.txt").write_text("a")
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: True)
        monkeypatch.setattr(
            "docking.applets.trash.backend._visible_trash_files_directory",
            lambda: files_dir,
        )

        assert KdeTrashBackend().count_items() == 1

    def test_kde_open_uses_host_command_in_flatpak(self, monkeypatch):
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: True)
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.host_command_available",
            lambda command: command == "dolphin",
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.host_command",
            lambda cmd: ["/usr/bin/flatpak-spawn", "--host", *cmd],
        )
        with patch("docking.applets.trash.backend.subprocess.Popen") as popen:
            KdeTrashBackend().open()

        popen.assert_called_once_with(
            ("/usr/bin/flatpak-spawn", "--host", "dolphin", "trash:/")
        )

    def test_confirmation_preference_reads_kiorc(self, tmp_path, monkeypatch):
        config_file = tmp_path / "kiorc"
        config_file.write_text("[Confirmations]\nConfirmEmptyTrash=false\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        backend = KdeTrashBackend()
        with patch.object(backend, "_delete_contents") as delete:
            backend.empty(lambda: False)

        delete.assert_called_once()

    def test_confirmation_preference_defaults_to_true_when_missing(
        self, tmp_path, monkeypatch
    ):
        (tmp_path / "kiorc").write_text("[Confirmations]\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        backend = KdeTrashBackend()
        with patch.object(backend, "_delete_contents") as delete:
            backend.empty(lambda: False)

        delete.assert_not_called()

    def test_asks_when_confirmation_enabled(self, tmp_path, monkeypatch):
        (tmp_path / "kiorc").write_text("[Confirmations]\nConfirmEmptyTrash=true\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

        backend = KdeTrashBackend()
        with patch.object(backend, "_delete_contents") as delete:
            backend.empty(lambda: True)

        delete.assert_called_once()

    def test_confirmation_preference_handles_parser_error(self, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/test-xdg/config")
        with patch.object(
            configparser.ConfigParser, "read", side_effect=configparser.Error("bad")
        ):
            backend = KdeTrashBackend()
            assert backend._confirmation_preference() is True

    def test_confirmation_preference_value_error(self, tmp_path, monkeypatch):
        config_file = tmp_path / "kiorc"
        config_file.write_text("[Confirmations]\nConfirmEmptyTrash=notabool\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        backend = KdeTrashBackend()
        assert backend._confirmation_preference() is True

    def test_available_open_command_non_flatpak(self, monkeypatch):
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: False)
        monkeypatch.setattr(
            "docking.applets.trash.backend.shutil.which",
            lambda cmd: cmd == "kioclient6",
        )
        result = KdeTrashBackend()._available_open_command()
        assert result == ("kioclient6", "exec", "trash:/")

    def test_available_open_command_flatpak_unavailable(self, monkeypatch):
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: True)
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.host_command_available",
            lambda cmd: False,
        )
        result = KdeTrashBackend()._available_open_command()
        assert result is None

    def test_kde_count_items_handles_oserror(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", "/nonexistent/path")
        assert KdeTrashBackend().count_items() == 0

    def test_kde_open_falls_back_to_gio_on_oserror(self, monkeypatch):
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: False)
        monkeypatch.setattr(
            "docking.applets.trash.backend.shutil.which",
            lambda cmd: cmd == "kioclient6",
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend.subprocess.Popen",
            lambda *a, **kw: (_ for _ in ()).throw(OSError("failed")),
        )
        with patch(
            "docking.applets.trash.backend.Gio.AppInfo.launch_default_for_uri"
        ) as launch:
            KdeTrashBackend().open()

        launch.assert_called_once_with("trash:/", None)

    def test_kde_delete_directory_contents_oserror(self, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", "/root/restricted")
        backend = KdeTrashBackend()
        with contextlib.suppress(Exception):
            backend._delete_contents()

    def test_kde_delete_path_oserror(self, monkeypatch):
        backend = KdeTrashBackend()
        mock_path = MagicMock(spec=Path)
        mock_path.is_dir.return_value = False
        mock_path.is_symlink.return_value = False
        mock_path.unlink.side_effect = OSError("permission denied")
        with contextlib.suppress(Exception):
            backend._delete_path(mock_path)

    def test_kde_empty_deletes_when_host_trash_succeeds(self, monkeypatch):
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: True)
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.host_command",
            lambda cmd: ["flatpak-spawn", "--host", *cmd],
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.spawn_path",
            lambda: "/usr/bin/flatpak-spawn",
        )
        subprocess_run = MagicMock()
        subprocess_run.return_value.returncode = 0
        monkeypatch.setattr(
            "docking.applets.trash.backend.subprocess.run",
            subprocess_run,
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend._empty_host_trash",
            lambda: True,
        )
        with patch.object(KdeTrashBackend, "_delete_contents") as delete:
            KdeTrashBackend().empty(lambda: False)
        delete.assert_not_called()

    def test_open_host_trash_uri_no_command_returns_false(self):
        """When flatpak.host_command returns None, _open_host_trash_uri returns False."""
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=True),
            patch(
                "docking.applets.trash.backend.flatpak.host_command",
                return_value=None,
            ),
        ):
            from docking.applets.trash.backend import _open_host_trash_uri

            result = _open_host_trash_uri(uri="trash:///")
            assert result is False

    def test_monitor_file_not_flatpak_uses_uri(self):
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=False),
            patch("docking.applets.trash.backend.Gio.File.new_for_uri") as new_for_uri,
            patch(
                "docking.applets.trash.backend.Gio.File.new_for_path"
            ) as new_for_path,
        ):
            GioTrashBackend().monitor_file()
        new_for_uri.assert_called_once_with("trash:///")
        new_for_path.assert_not_called()

    def test_gio_empty_when_confirmation_disabled(self):
        backend = GioTrashBackend()
        backend.confirmation_schema = (
            "org.test.schema",
            "/org/test/path/",
        )
        with (
            patch.object(backend, "_confirmation_preference", return_value=False),
            patch("docking.applets.trash.backend.Gio.File.new_for_uri") as new_for_uri,
        ):
            trash = MagicMock()
            trash.enumerate_children.return_value = MagicMock()
            trash.enumerate_children.return_value.next_file.return_value = None
            new_for_uri.return_value = trash
            backend.empty(lambda: False)

    def test_kde_monitor_file_not_flatpak(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        with (
            patch("docking.applets.trash.backend.is_flatpak", return_value=False),
            patch(
                "docking.applets.trash.backend.Gio.File.new_for_path"
            ) as new_for_path,
        ):
            KdeTrashBackend().monitor_file()
        new_for_path.assert_called_once()

    def test_kde_delete_directory_contents_file_not_found(self, tmp_path):
        backend = KdeTrashBackend()
        backend._delete_directory_contents(Path("/nonexistent_trash_path"))

    def test_kde_empty_confirmation_disabled_with_host_empty(self, monkeypatch):
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: True)
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.host_command",
            lambda cmd: ["flatpak-spawn", "--host", *cmd],
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.spawn_path",
            lambda: "/usr/bin/flatpak-spawn",
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend.subprocess.run",
            MagicMock(return_value=MagicMock(returncode=0)),
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend._empty_host_trash",
            lambda: True,
        )
        conf_backend = KdeTrashBackend()
        with (
            patch.object(conf_backend, "_confirmation_preference", return_value=False),
            patch.object(conf_backend, "_delete_contents") as delete,
        ):
            conf_backend.empty(lambda: True)
        delete.assert_not_called()

    def test_kde_open_uses_host_command_in_flatpak_with_available_command(
        self, monkeypatch
    ):
        monkeypatch.setattr("docking.applets.trash.backend.is_flatpak", lambda: True)
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.host_command_available",
            lambda cmd: True,
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend.flatpak.host_command",
            lambda cmd: ("/usr/bin/flatpak-spawn", "--host", *cmd),
        )
        monkeypatch.setattr(
            "docking.applets.trash.backend.shutil.which",
            lambda cmd: False,
        )
        with patch("docking.applets.trash.backend.subprocess.Popen") as popen:
            KdeTrashBackend().open()

        popen.assert_called_once_with(
            ("/usr/bin/flatpak-spawn", "--host", "kioclient6", "exec", "trash:/")
        )


class _StubBackend:
    name = "stub"
    uri = "trash:///"

    def __init__(self, item_count: int = 0) -> None:
        self.item_count = item_count
        self.monitor = MagicMock()
        self.monitor_file_mock = MagicMock()
        self.monitor_file_mock.monitor.return_value = self.monitor
        self.open_calls = 0
        self.empty_confirm: Callable[[], bool] | None = None

    def count_items(self) -> int:
        return self.item_count

    def monitor_file(self):
        return self.monitor_file_mock

    def open(self) -> None:
        self.open_calls += 1

    def empty(self, confirm: Callable[[], bool]) -> None:
        self.empty_confirm = confirm


def _make_applet(monkeypatch, backend: _StubBackend) -> TrashApplet:
    monkeypatch.setattr(
        trash_applet_mod,
        "select_trash_backend",
        lambda **_: backend,
    )
    return TrashApplet(48)


class TestTrashAppletIcon:
    def test_empty_trash_uses_empty_icon(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=0))

        pixbuf = applet.create_icon(48)

        assert pixbuf is not None
        assert applet.item.name == "No items in Trash"

    def test_full_trash_uses_full_icon(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=5))

        pixbuf = applet.create_icon(48)

        assert pixbuf is not None
        assert "5 items" in applet.item.name

    def test_single_item_singular(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=1))
        applet.create_icon(48)
        assert applet.item.name == "1 item in Trash"

    def test_system_icon_name_tracks_empty_state(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=0))

        assert applet.system_icon_name() == "user-trash"

    def test_system_icon_name_tracks_full_state(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=5))

        assert applet.system_icon_name() == "user-trash-full"


class TestTrashAppletMenu:
    def test_returns_two_items(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=0))
        items = applet.get_menu_items()
        assert [item.get_label() for item in items] == [
            "Open Trash",
            "",
            "Empty Trash",
        ]

    def test_empty_trash_insensitive_when_empty(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=0))
        items = applet.get_menu_items()
        assert not items[2].get_sensitive()

    def test_empty_trash_sensitive_when_full(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=3))
        items = applet.get_menu_items()
        assert items[2].get_sensitive()


class TestTrashAppletLifecycle:
    def test_start_sets_monitor_and_stop_cancels(self, monkeypatch):
        backend = _StubBackend()
        applet = _make_applet(monkeypatch, backend)

        applet.start(lambda: None)

        assert applet._monitor is backend.monitor
        backend.monitor.connect.assert_called_once()

        applet.stop()

        backend.monitor.cancel.assert_called_once()
        assert applet._monitor is None

    def test_start_handles_monitor_error(self, monkeypatch):
        from gi.repository import GLib

        backend = _StubBackend()
        backend.monitor_file_mock.monitor.side_effect = GLib.Error("monitor error")
        applet = _make_applet(monkeypatch, backend)

        applet.start(lambda: None)

        assert applet._monitor is None

    def test_on_clicked_delegates_to_backend(self, monkeypatch):
        backend = _StubBackend()
        applet = _make_applet(monkeypatch, backend)

        applet.on_clicked()

        assert backend.open_calls == 1

    def test_on_trash_changed_recounts_from_backend(self, monkeypatch):
        backend = _StubBackend(item_count=0)
        applet = _make_applet(monkeypatch, backend)
        backend.item_count = 4

        applet._on_trash_changed()

        assert applet._item_count == 4
        assert "4 items" in applet.item.name

    def test_empty_trash_delegates_confirmation_callback(self, monkeypatch):
        backend = _StubBackend(item_count=1)
        applet = _make_applet(monkeypatch, backend)

        applet._empty_trash()

        assert backend.empty_confirm == applet._confirm_empty_trash

    def test_confirm_empty_trash_dialog(self, monkeypatch):
        applet = _make_applet(monkeypatch, _StubBackend(item_count=1))
        dialog = MagicMock()
        dialog.run.return_value = trash_applet_mod.Gtk.ResponseType.OK

        with patch(
            "docking.applets.trash.applet.Gtk.MessageDialog",
            return_value=dialog,
        ):
            assert applet._confirm_empty_trash() is True

        dialog.destroy.assert_called_once()
