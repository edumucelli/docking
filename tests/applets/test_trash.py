"""Tests for the trash applet."""

from collections.abc import Callable
from unittest.mock import MagicMock, patch

from docking.applets.trash import applet as trash_applet_mod
from docking.applets.trash.applet import TrashApplet
from docking.applets.trash.backend import (
    CinnamonTrashBackend,
    GioTrashBackend,
    GnomeTrashBackend,
    KdeTrashBackend,
    MateTrashBackend,
    select_trash_backend,
)
from docking.platform.environment import Desktop


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

    def test_open_launches_default_trash_uri(self):
        with patch(
            "docking.applets.trash.backend.Gio.AppInfo.launch_default_for_uri"
        ) as launch:
            GioTrashBackend().open()

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
