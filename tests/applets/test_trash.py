"""Tests for the trash applet."""

from unittest.mock import patch, MagicMock

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
