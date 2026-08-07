"""Tests for the Recent Files applet."""

from unittest.mock import MagicMock, patch

from docking.applets.recentfiles.state import (
    MAX_LABEL_LEN,
    RecentEntry,
    tooltip_text,
    truncate_name,
)
from docking.core.config import Config


class TestState:
    def test_truncate_short_name_unchanged(self):
        # Given a short name
        # When
        result = truncate_name(text="notes.txt")
        # Then
        assert result == "notes.txt"

    def test_truncate_long_name_adds_ellipsis(self):
        # Given a name exceeding MAX_LABEL_LEN
        long_name = "a" * (MAX_LABEL_LEN + 10)
        # When
        result = truncate_name(text=long_name)
        # Then
        assert len(result) == MAX_LABEL_LEN
        assert result.endswith("\u2026")

    def test_truncate_exact_length_unchanged(self):
        # Given name exactly at limit
        name = "b" * MAX_LABEL_LEN
        # When / Then
        assert truncate_name(text=name) == name

    def test_tooltip_text_with_entries(self):
        # Given entries
        entries = [RecentEntry(name="report.pdf", uri="file:///report.pdf")]
        # When / Then
        assert tooltip_text(entries=entries) == "report.pdf"

    def test_tooltip_text_empty(self):
        # Given no entries
        # When / Then
        assert tooltip_text(entries=[]) == "No recent files"


class TestRender:
    def test_render_icon_returns_pixbuf(self):
        # Given
        from docking.applets.recentfiles.render import render_icon

        # When
        pixbuf = render_icon(size=48, has_files=True)
        # Then
        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48

    def test_render_icon_no_files(self):
        # Given
        from docking.applets.recentfiles.render import render_icon

        # When
        pixbuf = render_icon(size=32, has_files=False)
        # Then
        assert pixbuf is not None
        assert pixbuf.get_width() == 32

    def test_render_icon_various_sizes(self):
        from docking.applets.recentfiles.render import render_icon

        for size in (24, 48, 64, 128):
            pixbuf = render_icon(size=size, has_files=True)
            assert pixbuf is not None
            assert pixbuf.get_width() == size


class TestApplet:
    def _make_recent_info(
        self, name: str, uri: str, modified: int, exists: bool = True
    ):
        info = MagicMock()
        info.get_display_name.return_value = name
        info.get_uri.return_value = uri
        info.get_modified.return_value = modified
        info.exists.return_value = exists
        return info

    def test_creates_with_entries(self):
        # Given a RecentManager with items
        manager = MagicMock()
        manager.get_items.return_value = [
            self._make_recent_info("a.txt", "file:///a.txt", 200),
            self._make_recent_info("b.txt", "file:///b.txt", 100),
        ]
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

        # Then
        assert len(applet._entries) == 2
        assert applet._entries[0].name == "a.txt"
        assert applet.item.name == "a.txt"

    def test_creates_empty(self):
        # Given no recent items
        manager = MagicMock()
        manager.get_items.return_value = []
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

        # Then
        assert applet._entries == []
        assert applet.item.name == "No recent files"

    def test_refresh_entries_filters_nonexistent(self):
        # Given mix of existing and non-existing
        manager = MagicMock()
        manager.get_items.return_value = [
            self._make_recent_info("a.txt", "file:///a.txt", 200),
            self._make_recent_info("gone.txt", "file:///gone.txt", 300, exists=False),
        ]
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

        # Then only existing entries kept
        assert len(applet._entries) == 1
        assert applet._entries[0].name == "a.txt"

    def test_on_clicked_opens_most_recent(self):
        # Given
        manager = MagicMock()
        manager.get_items.return_value = [
            self._make_recent_info("a.txt", "file:///a.txt", 200),
        ]
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

        # When
        with patch(
            "docking.applets.recentfiles.applet.targets.open_target"
        ) as open_target:
            applet.on_clicked()

        # Then
        open_target.assert_called_once_with("file:///a.txt")

    def test_on_clicked_noop_when_empty(self):
        # Given no entries
        manager = MagicMock()
        manager.get_items.return_value = []
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

        # When
        with patch(
            "docking.applets.recentfiles.applet.targets.open_target"
        ) as open_target:
            applet.on_clicked()

        # Then
        open_target.assert_not_called()

    def test_on_clicked_handles_launch_error(self):
        # Given
        manager = MagicMock()
        manager.get_items.return_value = [
            self._make_recent_info("a.txt", "file:///a.txt", 200),
        ]
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

        # When / Then (no exception raised)
        with patch(
            "docking.applets.recentfiles.applet.targets.open_target",
            return_value=False,
        ):
            applet.on_clicked()

    def test_get_menu_items_with_entries(self):
        # Given
        manager = MagicMock()
        manager.get_items.return_value = [
            self._make_recent_info("a.txt", "file:///a.txt", 200),
            self._make_recent_info("b.txt", "file:///b.txt", 100),
        ]
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

        # When
        items = applet.get_menu_items()

        # Then: 2 entries + separator + clear = 4
        assert len(items) == 4

    def test_get_menu_items_empty(self):
        # Given
        manager = MagicMock()
        manager.get_items.return_value = []
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

        # When
        items = applet.get_menu_items()

        # Then: just the clear item (insensitive)
        assert len(items) == 1
        assert not items[0].get_sensitive()

    def test_start_connects_and_stop_disconnects(self):
        # Given
        manager = MagicMock()
        manager.get_items.return_value = []
        manager.connect.return_value = 42
        with patch(
            "docking.applets.recentfiles.applet.Gtk.RecentManager.get_default",
            return_value=manager,
        ):
            from docking.applets.recentfiles.applet import RecentFilesApplet

            applet = RecentFilesApplet(48, config=Config())

            # When start
            applet.start(lambda: None)
            # Then
            manager.connect.assert_called_once_with("changed", applet._on_changed)
            assert applet._signal_id == 42

            # When stop
            applet.stop()
            # Then
            manager.disconnect.assert_called_once_with(42)
            assert applet._signal_id is None
