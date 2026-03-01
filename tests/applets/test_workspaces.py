"""Tests for the workspaces applet."""

from unittest.mock import MagicMock

import cairo
import pytest

import docking.applets.workspaces as workspaces_mod
from docking.applets.workspaces import WorkspacesApplet, _render_grid


class TestRenderGrid:
    """_render_grid should draw workspace cells with the active one highlighted."""

    @pytest.mark.parametrize("count", [1, 2, 4, 6, 9])
    def test_renders_various_counts(self, count):
        size = 48
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr, size, count, active_num=0)
        data = surface.get_data()
        assert any(b != 0 for b in data)

    def test_zero_count_draws_nothing(self):
        size = 48
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr, size, 0, active_num=-1)
        data = surface.get_data()
        assert all(b == 0 for b in data)

    @pytest.mark.parametrize("size", [32, 48, 64, 96])
    def test_renders_at_various_sizes(self, size):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_grid(cr, size, 4, active_num=1)
        data = surface.get_data()
        assert any(b != 0 for b in data)

    def test_grid_layout_2_columns(self):
        # Given 4 workspaces -> 2 cols x 2 rows
        size = 100
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        # When rendered with active=2
        _render_grid(cr, size, 4, active_num=2)
        # Then surface has content (no crash, correct layout)
        data = surface.get_data()
        assert any(b != 0 for b in data)


class TestWorkspacesApplet:
    def test_creates_with_icon(self):
        # Given no Wnck screen, falls back to default 4-cell grid
        applet = WorkspacesApplet(48)
        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = WorkspacesApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size


class TestWorkspacesBehavior:
    def test_on_clicked_activates_next_workspace(self, monkeypatch):
        # Given
        applet = WorkspacesApplet(48)
        active = MagicMock()
        active.get_number.return_value = 1
        target = MagicMock()
        screen = MagicMock()
        screen.get_active_workspace.return_value = active
        screen.get_workspace_count.return_value = 4
        screen.get_workspace.return_value = target
        applet._screen = screen
        monkeypatch.setattr(workspaces_mod.Gtk, "get_current_event_time", lambda: 99)
        # When
        applet.on_clicked()
        # Then
        screen.get_workspace.assert_called_once_with(2)
        target.activate.assert_called_once_with(99)

    def test_on_clicked_no_screen_or_active_is_safe(self):
        # Given
        applet = WorkspacesApplet(48)
        applet._screen = None
        # When / Then
        applet.on_clicked()

        # Given
        applet._screen = MagicMock()
        applet._screen.get_active_workspace.return_value = None
        # When / Then
        applet.on_clicked()

    def test_on_scroll_switches_workspace(self, monkeypatch):
        # Given
        applet = WorkspacesApplet(48)
        active = MagicMock()
        active.get_number.return_value = 0
        target = MagicMock()
        screen = MagicMock()
        screen.get_active_workspace.return_value = active
        screen.get_workspace_count.return_value = 4
        screen.get_workspace.return_value = target
        applet._screen = screen
        monkeypatch.setattr(workspaces_mod.Gtk, "get_current_event_time", lambda: 7)
        # When
        applet.on_scroll(direction_up=False)
        # Then
        screen.get_workspace.assert_called_once_with(1)
        target.activate.assert_called_once_with(7)

    def test_get_menu_items_builds_radios_for_workspaces(self):
        # Given
        applet = WorkspacesApplet(48)
        ws0 = MagicMock()
        ws0.get_name.return_value = "One"
        ws0.get_number.return_value = 0
        ws1 = MagicMock()
        ws1.get_name.return_value = "Two"
        ws1.get_number.return_value = 1
        active = MagicMock()
        active.get_number.return_value = 1
        screen = MagicMock()
        screen.get_workspaces.return_value = [ws0, ws1]
        screen.get_active_workspace.return_value = active
        applet._screen = screen
        # When
        items = applet.get_menu_items()
        # Then
        assert len(items) == 2
        assert items[1].get_active()

    def test_start_and_stop_manage_screen_signal(self, monkeypatch):
        # Given
        applet = WorkspacesApplet(48)
        screen = MagicMock()
        screen.connect.return_value = 33
        monkeypatch.setattr(workspaces_mod.Wnck.Screen, "get_default", lambda: screen)
        refresh = MagicMock()
        monkeypatch.setattr(applet, "refresh_icon", refresh)
        # When
        applet.start(lambda: None)
        # Then
        screen.force_update.assert_called_once()
        assert applet._signal_id == 33
        refresh.assert_called_once()

        # When
        applet.stop()
        # Then
        screen.disconnect.assert_called_once_with(33)
        assert applet._signal_id == 0

    def test_on_workspace_activate_and_changed_refresh(self, monkeypatch):
        # Given
        applet = WorkspacesApplet(48)
        ws = MagicMock()
        monkeypatch.setattr(workspaces_mod.Gtk, "get_current_event_time", lambda: 11)
        refresh = MagicMock()
        monkeypatch.setattr(applet, "refresh_icon", refresh)
        # When
        applet._on_workspace_activate(MagicMock(), ws)
        applet._on_workspace_changed(MagicMock())
        # Then
        ws.activate.assert_called_once_with(11)
        refresh.assert_called_once()
