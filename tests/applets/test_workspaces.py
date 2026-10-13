"""Tests for the workspaces applet."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import cairo
import pytest

from docking.applets.services import AppletServices
from docking.applets.workspaces import applet as applet_module
from docking.applets.workspaces.applet import WorkspacesApplet
from docking.applets.workspaces.render import _render_grid
from docking.core.config import Config
from docking.platform.backends.base import ActionResult, WorkspaceSnapshot
from docking.platform.backends.wayland.sway_ipc import SwayWorkspaceService


def _workspace(
    number: int,
    *,
    workspace_id: str | None = None,
    name: str = "",
    active: bool = False,
) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        id=workspace_id or str(number),
        number=number,
        name=name,
        active=active,
    )


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
        applet = WorkspacesApplet(48, config=Config())
        assert applet.item.icon is not None

    def test_icon_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = WorkspacesApplet(size, config=Config())
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None
            assert pixbuf.get_width() == size

    def test_item_name_uses_workspace_number_even_if_wnck_name_is_stale(self):
        # Given
        applet = WorkspacesApplet(48, config=Config())
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
        applet = WorkspacesApplet(48, config=Config())
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

    def test_on_clicked_activates_next_workspace_by_snapshot_id(self):
        # Given
        applet = WorkspacesApplet(48, config=Config())
        service = MagicMock()
        service.list_workspaces.return_value = [
            _workspace(0, workspace_id="first"),
            _workspace(1, workspace_id="second", active=True),
            _workspace(2, workspace_id="target"),
        ]
        applet.set_services(AppletServices(workspaces=service))
        # When
        applet.on_clicked()
        # Then
        service.activate.assert_called_once_with("target")

    def test_on_clicked_no_screen_or_active_is_safe(self):
        # Given
        applet = WorkspacesApplet(48, config=Config())
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
        applet = WorkspacesApplet(48, config=Config())
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

    def test_on_scroll_activates_workspace_by_snapshot_id(self):
        # Given
        applet = WorkspacesApplet(48, config=Config())
        service = MagicMock()
        service.list_workspaces.return_value = [
            _workspace(0, workspace_id="previous"),
            _workspace(1, workspace_id="current", active=True),
            _workspace(2, workspace_id="next"),
        ]
        applet.set_services(AppletServices(workspaces=service))
        # When
        applet.on_scroll(direction_up=True)
        # Then
        service.activate.assert_called_once_with("previous")

    def test_get_menu_items_builds_radios_for_workspaces(self):
        # Given
        applet = WorkspacesApplet(48, config=Config())
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
        applet = WorkspacesApplet(48, config=Config())
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

    def test_start_is_idempotent_for_workspace_watch(self, monkeypatch):
        applet = WorkspacesApplet(48, config=Config())
        service = MagicMock()
        handle = object()
        service.list_workspaces.return_value = []
        service.active_workspace.return_value = None
        service.watch_active_workspace.return_value = handle
        applet.set_services(AppletServices(workspaces=service))
        refresh = MagicMock()
        monkeypatch.setattr(applet, "present", refresh)

        applet.start(lambda: None)
        applet.start(lambda: None)

        service.watch_active_workspace.assert_called_once_with(
            applet._on_workspace_changed
        )
        assert applet._watch_handle is handle
        assert refresh.call_count == 2

    def test_set_services_rewatches_when_already_started(self, monkeypatch):
        applet = WorkspacesApplet(48, config=Config())
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
        applet = WorkspacesApplet(48, config=Config())
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


class TestWorkspaceNumbering:
    """Backends disagree on ``WorkspaceSnapshot.number``; the applet must not.

    libwnck, GNOME, Cinnamon, Niri and the Wayland portal count workspaces from
    0, while Sway, KWin, Hyprland and Wayfire count from 1, so activation,
    scrolling, the highlight and the label all have to follow the position of
    the active workspace in the snapshot list.
    """

    @staticmethod
    def _one_based(*, active_index: int) -> list[WorkspaceSnapshot]:
        # Ids mirror the numbers, the way Sway ("1"), KWin and Hyprland report.
        return [
            _workspace(number, name=f"ws{number}", active=(index == active_index))
            for index, number in enumerate((1, 2, 3, 4))
        ]

    @staticmethod
    def _applet(snapshots) -> tuple[WorkspacesApplet, MagicMock]:
        applet = WorkspacesApplet(48, config=Config())
        service = MagicMock()
        service.list_workspaces.return_value = snapshots
        service.active_workspace.return_value = next(
            (snapshot for snapshot in snapshots if snapshot.active), None
        )
        applet.set_services(AppletServices(workspaces=service))
        return applet, service

    def test_click_activates_the_next_workspace(self):
        applet, service = self._applet(self._one_based(active_index=0))

        applet.on_clicked()

        service.activate.assert_called_once_with("2")

    def test_scroll_up_wraps_from_the_first_workspace_to_the_last(self):
        applet, service = self._applet(self._one_based(active_index=0))

        applet.on_scroll(direction_up=True)

        service.activate.assert_called_once_with("4")

    def test_scroll_down_activates_the_next_workspace(self):
        applet, service = self._applet(self._one_based(active_index=1))

        applet.on_scroll(direction_up=False)

        service.activate.assert_called_once_with("3")

    def test_click_on_the_last_workspace_wraps_to_the_first(self):
        applet, service = self._applet(self._one_based(active_index=3))

        applet.on_clicked()

        service.activate.assert_called_once_with("1")

    def test_tooltip_names_the_active_workspace(self):
        applet, _service = self._applet(self._one_based(active_index=1))

        applet.present()

        assert applet.item.name == "Workspace 2: ws2"

    def test_menu_labels_number_workspaces_by_position(self):
        applet, _service = self._applet(self._one_based(active_index=0))

        items = applet.get_menu_items()

        assert [item.get_label() for item in items] == [
            "Workspace 1: ws1",
            "Workspace 2: ws2",
            "Workspace 3: ws3",
            "Workspace 4: ws4",
        ]

    def _highlighted(self, monkeypatch, snapshots) -> list[int]:
        highlighted: list[int] = []
        monkeypatch.setattr(
            applet_module,
            "_render_grid",
            lambda *, cr, size, count, active_num: highlighted.append(active_num),
        )
        applet, _service = self._applet(snapshots)
        applet.present()
        return highlighted

    def test_highlight_is_the_active_position(self, monkeypatch):
        highlighted = self._highlighted(monkeypatch, self._one_based(active_index=0))

        assert highlighted[-1] == 0

    def test_last_workspace_is_highlighted_when_active(self, monkeypatch):
        # Regression: a 1-based number left the last cell unhighlighted because
        # the index fell outside the grid.
        highlighted = self._highlighted(monkeypatch, self._one_based(active_index=3))

        assert highlighted[-1] == 3

    def test_sway_snapshots_drive_activation_by_position(self):
        # Sway numbers workspaces from 1; end to end, the applet must still
        # activate the workspace next to the one the user sees highlighted.
        client = SimpleNamespace(
            query=lambda kind: [
                {"id": 1, "name": "1", "focused": True},
                {"id": 2, "name": "2", "focused": False},
                {"id": 3, "name": "3", "focused": False},
            ],
            command=MagicMock(return_value=ActionResult.OK),
        )
        service = SwayWorkspaceService(client=client, windows=MagicMock())
        applet = WorkspacesApplet(48, config=Config())

        applet.set_services(AppletServices(workspaces=service))
        applet.present()
        applet.on_clicked()

        assert applet.item.name == "Workspace 1: 1"
        client.command.assert_called_once_with('workspace "2"')
