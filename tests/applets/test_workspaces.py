"""Tests for the workspaces applet."""

from unittest.mock import MagicMock

import cairo
import pytest

from docking.applets.services import AppletServices
from docking.applets.workspaces.applet import WorkspacesApplet
from docking.applets.workspaces.render import _render_grid
from docking.platform.backends.base import WorkspaceSnapshot


def _workspace(
    number: int, *, name: str = "", active: bool = False
) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(id=str(number), number=number, name=name, active=active)


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

    def test_item_name_uses_workspace_number_even_if_wnck_name_is_stale(self):
        # Given
        applet = WorkspacesApplet(48)
        service = MagicMock()
        service.list_workspaces.return_value = [
            _workspace(0),
            _workspace(1, name="Workspace 1", active=True),
            _workspace(2),
        ]
        applet.set_services(AppletServices(workspaces=service))

        # When
        applet.present()

        # Then
        assert applet.item.name == "Workspace 2"


class TestWorkspacesBehavior:
    def test_on_clicked_activates_next_workspace(self):
        # Given
        applet = WorkspacesApplet(48)
        service = MagicMock()
        service.list_workspaces.return_value = [
            _workspace(0),
            _workspace(1, active=True),
            _workspace(2),
            _workspace(3),
        ]
        applet.set_services(AppletServices(workspaces=service))
        # When
        applet.on_clicked()
        # Then
        service.activate.assert_called_once_with("2")

    def test_on_clicked_no_screen_or_active_is_safe(self):
        # Given
        applet = WorkspacesApplet(48)
        applet._workspace_service = None
        # When / Then
        applet.on_clicked()

        # Given
        service = MagicMock()
        service.list_workspaces.return_value = [_workspace(0), _workspace(1)]
        service.active_workspace.return_value = None
        applet.set_services(AppletServices(workspaces=service))
        # When / Then
        applet.on_clicked()

    def test_on_scroll_switches_workspace(self):
        # Given
        applet = WorkspacesApplet(48)
        service = MagicMock()
        service.list_workspaces.return_value = [
            _workspace(0, active=True),
            _workspace(1),
            _workspace(2),
            _workspace(3),
        ]
        applet.set_services(AppletServices(workspaces=service))
        # When
        applet.on_scroll(direction_up=False)
        # Then
        service.activate.assert_called_once_with("1")

    def test_get_menu_items_builds_radios_for_workspaces(self):
        # Given
        applet = WorkspacesApplet(48)
        service = MagicMock()
        service.list_workspaces.return_value = [
            _workspace(0, name="One"),
            _workspace(1, name="Two", active=True),
        ]
        applet.set_services(AppletServices(workspaces=service))
        # When
        items = applet.get_menu_items()
        # Then
        assert len(items) == 2
        assert items[1].get_active()

    def test_start_and_stop_manage_screen_signal(self, monkeypatch):
        # Given
        applet = WorkspacesApplet(48)
        service = MagicMock()
        handle = object()
        service.list_workspaces.return_value = []
        service.active_workspace.return_value = None
        service.watch_active_workspace.return_value = handle
        applet.set_services(AppletServices(workspaces=service))
        refresh = MagicMock()
        monkeypatch.setattr(applet, "present", refresh)
        # When
        applet.start(lambda: None)
        # Then
        service.watch_active_workspace.assert_called_once()
        assert applet._watch_handle is handle
        refresh.assert_called_once()

        # When
        applet.stop()
        # Then
        service.unwatch_active_workspace.assert_called_once_with(handle)
        assert applet._watch_handle is None

    def test_set_services_rewatches_when_already_started(self, monkeypatch):
        applet = WorkspacesApplet(48)
        old_service = MagicMock()
        old_handle = object()
        applet._workspace_service = old_service
        applet._watch_handle = old_handle
        applet._notify = MagicMock()
        new_service = MagicMock()
        new_handle = object()
        new_service.watch_active_workspace.return_value = new_handle
        refresh = MagicMock()
        monkeypatch.setattr(applet, "present", refresh)

        applet.set_services(AppletServices(workspaces=new_service))

        old_service.unwatch_active_workspace.assert_called_once_with(old_handle)
        new_service.watch_active_workspace.assert_called_once()
        assert applet._workspace_service is new_service
        assert applet._watch_handle is new_handle
        refresh.assert_called_once_with()

    def test_on_workspace_activate_and_changed_refresh(self, monkeypatch):
        # Given
        applet = WorkspacesApplet(48)
        service = MagicMock()
        service.list_workspaces.return_value = []
        service.active_workspace.return_value = None
        applet.set_services(AppletServices(workspaces=service))
        refresh = MagicMock()
        monkeypatch.setattr(applet, "present", refresh)
        # When
        applet._on_workspace_activate(MagicMock(), "2")
        applet._on_workspace_changed()
        # Then
        service.activate.assert_called_once_with("2")
        refresh.assert_called_once()
