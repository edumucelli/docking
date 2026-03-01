"""Tests for the trash applet."""

from unittest.mock import MagicMock, patch

from docking.applets.trash import TrashApplet, _count_trash_items


class TestCountTrashItems:
    def test_counts_items(self):
        # Given an enumerator yielding 3 items
        mock_enum = MagicMock()
        mock_enum.next_file.side_effect = [MagicMock(), MagicMock(), MagicMock(), None]
        mock_file = MagicMock()
        mock_file.enumerate_children.return_value = mock_enum

        # When
        with patch(
            "docking.applets.trash.Gio.File.new_for_uri", return_value=mock_file
        ):
            count = _count_trash_items()

        # Then
        assert count == 3

    def test_returns_zero_on_error(self):
        # Given enumerate_children raises
        from gi.repository import GLib

        mock_file = MagicMock()
        mock_file.enumerate_children.side_effect = GLib.Error("fail")

        with patch(
            "docking.applets.trash.Gio.File.new_for_uri", return_value=mock_file
        ):
            assert _count_trash_items() == 0


class TestTrashAppletIcon:
    def test_empty_trash_uses_empty_icon(self):
        # Given empty trash
        with patch("docking.applets.trash._count_trash_items", return_value=0):
            applet = TrashApplet(48)

        # When
        pixbuf = applet.create_icon(48)

        # Then
        assert pixbuf is not None
        assert applet.item.name == "No items in Trash"

    def test_full_trash_uses_full_icon(self):
        # Given trash with items
        with patch("docking.applets.trash._count_trash_items", return_value=5):
            applet = TrashApplet(48)

        # When
        pixbuf = applet.create_icon(48)

        # Then
        assert pixbuf is not None
        assert "5 items" in applet.item.name

    def test_single_item_singular(self):
        with patch("docking.applets.trash._count_trash_items", return_value=1):
            applet = TrashApplet(48)
        applet.create_icon(48)
        assert applet.item.name == "1 item in Trash"


class TestTrashAppletMenu:
    def test_returns_two_items(self):
        with patch("docking.applets.trash._count_trash_items", return_value=0):
            applet = TrashApplet(48)
        items = applet.get_menu_items()
        assert len(items) == 2

    def test_empty_trash_insensitive_when_empty(self):
        with patch("docking.applets.trash._count_trash_items", return_value=0):
            applet = TrashApplet(48)
        items = applet.get_menu_items()
        empty_item = items[1]
        assert not empty_item.get_sensitive()

    def test_empty_trash_sensitive_when_full(self):
        with patch("docking.applets.trash._count_trash_items", return_value=3):
            applet = TrashApplet(48)
        items = applet.get_menu_items()
        empty_item = items[1]
        assert empty_item.get_sensitive()


class TestTrashAppletLifecycle:
    def test_start_sets_monitor_and_stop_cancels(self):
        # Given
        with patch("docking.applets.trash._count_trash_items", return_value=0):
            applet = TrashApplet(48)
        monitor = MagicMock()
        trash = MagicMock()
        trash.monitor.return_value = monitor
        with patch("docking.applets.trash.Gio.File.new_for_uri", return_value=trash):
            # When
            applet.start(lambda: None)
            # Then
            assert applet._monitor is monitor
            monitor.connect.assert_called_once()

            # When
            applet.stop()
            # Then
            monitor.cancel.assert_called_once()
            assert applet._monitor is None

    def test_start_handles_monitor_error(self):
        # Given
        from gi.repository import GLib

        with patch("docking.applets.trash._count_trash_items", return_value=0):
            applet = TrashApplet(48)
        trash = MagicMock()
        trash.monitor.side_effect = GLib.Error("monitor error")
        with patch("docking.applets.trash.Gio.File.new_for_uri", return_value=trash):
            # When
            applet.start(lambda: None)
            # Then
            assert applet._monitor is None

    def test_on_clicked_handles_launch_error(self):
        # Given
        from gi.repository import GLib

        with patch("docking.applets.trash._count_trash_items", return_value=0):
            applet = TrashApplet(48)
        with patch(
            "docking.applets.trash.Gio.AppInfo.launch_default_for_uri",
            side_effect=GLib.Error("boom"),
        ):
            # When / Then
            applet.on_clicked()


class TestTrashAppletDeletePaths:
    def test_empty_trash_uses_dbus_first(self):
        # Given
        with patch("docking.applets.trash._count_trash_items", return_value=1):
            applet = TrashApplet(48)
        bus = MagicMock()
        with patch("docking.applets.trash.Gio.bus_get_sync", return_value=bus):
            # When
            applet._empty_trash()
            # Then
            bus.call_sync.assert_called_once()

    def test_empty_trash_falls_back_to_delete(self):
        # Given
        from gi.repository import GLib

        with patch("docking.applets.trash._count_trash_items", return_value=1):
            applet = TrashApplet(48)
        bus = MagicMock()
        bus.call_sync.side_effect = [GLib.Error("caja"), GLib.Error("nautilus")]
        with patch("docking.applets.trash.Gio.bus_get_sync", return_value=bus):
            with patch.object(applet, "_delete_trash_contents") as delete_mock:
                # When
                applet._empty_trash()
                # Then
                delete_mock.assert_called_once()

    def test_delete_trash_contents_deletes_children(self):
        # Given
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
        with patch("docking.applets.trash.Gio.File.new_for_uri", return_value=trash):
            # When
            with patch("docking.applets.trash._count_trash_items", return_value=2):
                applet = TrashApplet(48)
            applet._delete_trash_contents()
            # Then
            child_a.delete.assert_called_once()
            child_b.delete.assert_called_once()
            enumerator.close.assert_called_once()

    def test_delete_trash_contents_ignores_delete_errors(self):
        # Given
        from gi.repository import GLib

        info = MagicMock()
        info.get_name.return_value = "bad.txt"
        enumerator = MagicMock()
        enumerator.next_file.side_effect = [info, None]
        child = MagicMock()
        child.delete.side_effect = GLib.Error("cannot delete")
        trash = MagicMock()
        trash.enumerate_children.return_value = enumerator
        trash.get_child.return_value = child
        with patch("docking.applets.trash.Gio.File.new_for_uri", return_value=trash):
            # When
            with patch("docking.applets.trash._count_trash_items", return_value=1):
                applet = TrashApplet(48)
            applet._delete_trash_contents()
            # Then
            child.delete.assert_called_once()
            enumerator.close.assert_called_once()
