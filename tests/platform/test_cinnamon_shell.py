"""Cinnamon positioning when the compositor does not implement layer-shell."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.position import Position
from docking.platform.backends import selection
from docking.platform.backends.base import (
    DisplayServer,
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    Size,
)
from docking.platform.backends.cinnamon import shell
from docking.platform.backends.cinnamon.session import CinnamonShellSessionBackend
from docking.platform.backends.cinnamon.windows import CinnamonWindowService
from tests.platform.application_fakes import identity_services


def _request(x=0, y=853):
    return PlacementRequest(
        monitor=MonitorSnapshot(0, Rect(0, 0, 1920, 1080)),
        position="bottom",
        x=x,
        y=y,
        size=Size(1920, 187),
    )


def _proxy(value, success=True):
    return SimpleNamespace(
        call_sync=MagicMock(
            return_value=SimpleNamespace(unpack=lambda: (success, json.dumps(value)))
        )
    )


def test_shell_client_positions_only_the_identified_dock_and_returns_actual_position():
    proxy = _proxy([0, 853])
    client = shell.CinnamonShellClient(proxy=proxy)
    title = 'Docking ["quoted\\identifier]'
    assert client.position_dock(
        title=title, request=_request(y=893), current_workspace_only=False
    ) == (0, 853)
    script = proxy.call_sync.call_args.args[1].unpack()[0]
    assert f"w.get_title() === {json.dumps(title)}" in script
    assert "move_resize_frame(true, 0, 893, 1920, 187)" in script
    assert "w.stick()" in script
    assert "w.make_above()" in script
    assert proxy.call_sync.call_args.args[3] == 250


def test_shell_client_honors_workspace_and_stacking_preferences():
    proxy = _proxy([0, 853])
    client = shell.CinnamonShellClient(proxy=proxy)
    client.position_dock(
        title="Docking [unique]",
        request=replace(_request(), keep_above=False),
        current_workspace_only=True,
    )
    script = proxy.call_sync.call_args.args[1].unpack()[0]
    assert "w.unstick()" in script
    assert "w.unmake_above()" in script


@pytest.mark.parametrize("value", [None, False, [], [0], [0, "853"], [True, 853]])
def test_shell_client_rejects_missing_or_invalid_window_position(value):
    client = shell.CinnamonShellClient(proxy=_proxy(value))
    assert (
        client.position_dock(
            title="Docking [unique]", request=_request(), current_workspace_only=True
        )
        is None
    )


def test_shell_client_handles_disabled_eval_and_bus_failures():
    proxy = _proxy([0, 853], success=False)
    client = shell.CinnamonShellClient(proxy=proxy)
    assert client.workarea(_request().monitor) is None
    proxy.call_sync.side_effect = RuntimeError("shell restarted")
    assert client.workarea(_request().monitor) is None


def test_shell_client_probes_eval_before_selecting_backend(monkeypatch):
    proxy = _proxy({"windows": True, **dict.fromkeys(shell.NATIVE_FEATURES, True)})
    monkeypatch.setattr(shell.Gio.DBusProxy, "new_for_bus_sync", lambda *_: proxy)
    assert shell.CinnamonShellClient.connect() is not None
    proxy.call_sync.return_value = SimpleNamespace(unpack=lambda: (False, ""))
    assert shell.CinnamonShellClient.connect() is None


@pytest.mark.parametrize(
    "value", [None, [], [0, 0, 1920], [0, 0, -1, 1040], [0, 0, 1920, True]]
)
def test_shell_client_rejects_invalid_workareas(value):
    assert (
        shell.CinnamonShellClient(proxy=_proxy(value)).workarea(_request().monitor)
        is None
    )


def test_shell_client_reads_panel_workarea_for_target_monitor():
    proxy = _proxy([1920, 0, 1920, 1040])
    monitor = MonitorSnapshot(0, Rect(1920, 0, 1920, 1080))
    assert shell.CinnamonShellClient(proxy=proxy).workarea(monitor) == Rect(
        1920, 0, 1920, 1040
    )
    script = proxy.call_sync.call_args.args[1].unpack()[0]
    assert "wanted = [1920, 0, 1920, 1080]" in script
    assert "get_monitor_geometry(i)" in script
    assert "get_work_area_for_monitor(index)" in script


@pytest.mark.parametrize(
    ("edge", "rect"),
    [
        (Position.BOTTOM, [1920, 982, 1920, 98]),
        (Position.TOP, [1920, 0, 1920, 98]),
        (Position.LEFT, [1920, 0, 98, 1080]),
        (Position.RIGHT, [3742, 0, 98, 1080]),
    ],
)
def test_shell_reservation_includes_gap_and_panel_but_not_animation_surface(edge, rect):
    proxy = _proxy(True)
    client = shell.CinnamonShellClient(proxy=proxy)
    request = ReservationRequest(
        monitor=MonitorSnapshot(0, Rect(1920, 0, 1920, 1080)),
        position=edge,
        thickness=58,
        edge_offset=40,
    )
    assert client.reserve_dock(title='Docking ["owned"]', request=request)
    script = proxy.call_sync.call_args.args[1].unpack()[0]
    assert f"const r = {json.dumps(rect)}" in script
    assert "affectsInputRegion:false" in script
    assert "connect('unmanaged'" in script


def test_shell_external_workarea_excludes_only_own_reservation_and_restores_it():
    proxy = _proxy([0, 0, 1920, 1040])
    client = shell.CinnamonShellClient(proxy=proxy)
    assert client.workarea(_request().monitor, exclude_title="Docking [owned]") == Rect(
        0, 0, 1920, 1040
    )
    script = proxy.call_sync.call_args.args[1].unpack()[0]
    assert 'global._dockingReservations?.["Docking [owned]"]' in script
    assert "affectsStruts:false" in script
    assert "finally" in script
    assert "affectsStruts:true" in script


def test_surface_retries_reservation_after_mapping_and_clears_on_stop(monkeypatch):
    monkeypatch.setattr(shell.GLib, "timeout_add", lambda *_: 42)
    monkeypatch.setattr(shell.GLib, "source_remove", lambda *_: None)
    client = SimpleNamespace(
        last_query_failed=False,
        rows=(),
        snapshot_revision=0,
        clear_dock_visibility=MagicMock(),
        position_dock=MagicMock(side_effect=[None, (0, 853)]),
        reserve_dock=MagicMock(side_effect=[False, True]),
        clear_reservation=MagicMock(),
    )
    surface = shell.CinnamonShellSurfaceService(client=client)
    surface.configure_before_realize(MagicMock())
    surface.position_or_anchor(_request())
    request = ReservationRequest(_request().monitor, Position.BOTTOM, 53)
    surface.set_reservation(request)
    assert client.reserve_dock.call_count == 1
    surface._retry_position()
    assert client.reserve_dock.call_count == 2
    surface.stop()
    client.clear_reservation.assert_called_once_with(title=surface._title)
    assert surface._reservation is None


def test_surface_retries_mapping_and_uses_latest_placement(monkeypatch):
    timeout = MagicMock(return_value=42)
    remove = MagicMock()
    monkeypatch.setattr(shell.GLib, "timeout_add", timeout)
    monkeypatch.setattr(shell.GLib, "source_remove", remove)
    client = SimpleNamespace(
        last_query_failed=False,
        rows=(),
        snapshot_revision=0,
        clear_dock_visibility=MagicMock(),
        position_dock=MagicMock(side_effect=[None, (0, 853), (1733, 0)]),
    )
    surface = shell.CinnamonShellSurfaceService(client=client)
    window = MagicMock()
    surface.configure_before_realize(window)
    surface.position_or_anchor(_request())
    assert surface.get_surface_position() is None
    assert surface._retry_position() is True
    assert surface.get_surface_position() == (0, 853)
    surface.position_or_anchor(_request(x=1733, y=0))
    assert surface.get_surface_position() == (1733, 0)
    assert client.position_dock.call_args.kwargs["request"].x == 1733
    timeout.assert_called_once()
    window.move.assert_not_called()
    assert window.set_title.call_args.args[0].startswith("Docking [")
    surface.stop()
    remove.assert_called_once_with(42)
    assert surface.get_surface_position() is None
    assert surface._request is None


def test_surface_bounds_retries_and_cancels_after_stop(monkeypatch):
    monkeypatch.setattr(shell.GLib, "timeout_add", lambda *_: 42)
    monkeypatch.setattr(shell.GLib, "source_remove", lambda *_: None)
    client = SimpleNamespace(
        position_dock=MagicMock(return_value=None), clear_dock_visibility=MagicMock()
    )
    surface = shell.CinnamonShellSurfaceService(client=client)
    surface.configure_before_realize(MagicMock())
    surface.position_or_anchor(_request())
    for _ in range(19):
        assert surface._retry_position() is True
    assert surface._retry_position() is False
    assert client.position_dock.call_count == 21
    surface.stop()
    surface._retry_position()
    assert client.position_dock.call_count == 21


def test_surface_keeps_workspace_scope_and_popup_coordinates(monkeypatch):
    monkeypatch.setattr(shell.GLib, "timeout_add", lambda *_: 42)
    client = SimpleNamespace(
        position_dock=MagicMock(return_value=(0, 853)),
        snapshot_revision=0,
        last_query_failed=False,
        rows=(),
    )
    surface = shell.CinnamonShellSurfaceService(client=client)
    surface.configure_before_realize(MagicMock())
    surface.position_or_anchor(_request())
    surface.set_workspace_scope(current_workspace_only=True)
    assert client.position_dock.call_args.kwargs["current_workspace_only"] is True
    assert surface.popups_use_parent_relative_coordinates is True


def test_selection_uses_shell_when_layer_shell_is_unsupported(monkeypatch):
    monkeypatch.setattr(
        "docking.platform.backends.wayland.services.load_gtk_layer_shell", lambda: None
    )
    client = MagicMock()
    client.features = shell.NATIVE_FEATURES
    monkeypatch.setattr(shell.CinnamonShellClient, "connect", lambda: client)
    backend = selection._create_cinnamon_wayland_backend(
        model=MagicMock(), reason="native Wayland", **identity_services()
    )
    assert isinstance(backend, CinnamonShellSessionBackend)
    assert backend.name == "cinnamon-shell"
    assert backend.display_server is DisplayServer.WAYLAND
    assert backend.capabilities.supports_layer_shell is False
    assert backend.capabilities.supports_screen_reservation is True
    assert backend.capabilities.tracks_windows is True
    assert backend.capabilities.supports_activate is True
    assert backend.capabilities.supports_minimize is True
    assert backend.capabilities.supports_close is True
    assert isinstance(backend.windows, CinnamonWindowService)
    assert isinstance(backend.surface, shell.CinnamonShellSurfaceService)
    from docking.platform.backends.cinnamon.services import (
        CinnamonPreviewService,
        CinnamonVisibilityService,
        CinnamonWorkspaceService,
    )

    assert isinstance(backend.previews, CinnamonPreviewService)
    assert isinstance(backend.visibility, CinnamonVisibilityService)
    assert isinstance(backend.workspaces, CinnamonWorkspaceService)
    assert backend.desktop_actions is not None
    assert backend.window_picker is not None
    assert backend.capabilities.supports_current_workspace_filter
    assert backend.capabilities.supports_show_desktop
    assert backend.capabilities.supports_any_overlap
    assert backend.capabilities.supports_window_pick


def test_shell_backend_lifecycle_tracks_windows_and_releases_reservation(monkeypatch):
    schedule = MagicMock(return_value=42)
    remove = MagicMock()
    monkeypatch.setattr(shell.GLib, "timeout_add", schedule)
    monkeypatch.setattr(shell.GLib, "source_remove", remove)
    client = SimpleNamespace(
        last_query_failed=False,
        features=shell.NATIVE_FEATURES,
        rows=(),
        snapshot_revision=0,
        list_windows=MagicMock(return_value=()),
        reserve_dock=MagicMock(),
        clear_reservation=MagicMock(),
        clear_dock_visibility=MagicMock(),
    )
    model = MagicMock()
    model.visible_items.return_value = []
    backend = CinnamonShellSessionBackend(
        client=client, model=model, **identity_services()
    )

    backend.start()
    backend.start()
    assert backend.windows.diagnostic_snapshot().status == "available"
    client.list_windows.assert_called_once_with()
    schedule.assert_called_once()
    request = ReservationRequest(
        monitor=_request().monitor, position=Position.BOTTOM, thickness=48
    )
    backend.surface.set_reservation(request)
    backend.stop()
    backend.stop()

    remove.assert_called_once_with(42)
    client.clear_reservation.assert_called_once_with(
        title=client.reserve_dock.call_args.kwargs["title"]
    )
    assert backend.windows.diagnostic_snapshot().status == "stopped"
    assert model.update_running.call_args.kwargs["running"] == {}


def test_selection_retains_fallback_when_cinnamon_api_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        "docking.platform.backends.wayland.services.load_gtk_layer_shell", lambda: None
    )
    monkeypatch.setattr(shell.CinnamonShellClient, "connect", lambda: None)
    assert (
        selection._create_cinnamon_wayland_backend(
            model=MagicMock(), reason="native Wayland", **identity_services()
        )
        is None
    )


def test_selection_uses_shell_windows_with_layer_shell_on_newer_muffin(monkeypatch):
    from tests.platform.test_cinnamon_wayland import _layer_shell

    layer_shell = _layer_shell()
    monkeypatch.setattr(
        "docking.platform.backends.wayland.services.load_gtk_layer_shell",
        lambda: layer_shell,
    )
    monkeypatch.setattr(
        "docking.platform.backends.wayland.services.layer_shell_is_supported",
        lambda _: True,
    )
    shell_connect = MagicMock()
    shell_connect.return_value.features = shell.NATIVE_FEATURES
    monkeypatch.setattr(shell.CinnamonShellClient, "connect", shell_connect)
    monkeypatch.setattr(
        "docking.platform.backends.wayland.session.WaylandProtocolRuntime.start",
        lambda _: False,
    )
    from docking.platform.backends.cinnamon.muffin import MuffinDebugClient

    monkeypatch.setattr(MuffinDebugClient, "connect", lambda: None)
    backend = selection._create_cinnamon_wayland_backend(
        model=MagicMock(), reason="native Wayland", **identity_services()
    )
    assert backend is not None
    assert backend.name == "cinnamon-wayland"
    assert backend.capabilities.supports_layer_shell
    assert backend.capabilities.supports_activate
    shell_connect.assert_called_once()


def test_selection_retains_read_only_muffin_when_shell_is_unavailable(monkeypatch):
    from docking.platform.backends.cinnamon.muffin import (
        MuffinDebugClient,
        MuffinWindowService,
    )
    from tests.platform.test_cinnamon_wayland import _layer_shell

    monkeypatch.setattr(
        "docking.platform.backends.wayland.services.load_gtk_layer_shell", _layer_shell
    )
    monkeypatch.setattr(
        "docking.platform.backends.wayland.services.layer_shell_is_supported",
        lambda _: True,
    )
    monkeypatch.setattr(
        "docking.platform.backends.wayland.session.WaylandProtocolRuntime.start",
        lambda _: False,
    )
    monkeypatch.setattr(shell.CinnamonShellClient, "connect", lambda: None)
    monkeypatch.setattr(MuffinDebugClient, "connect", lambda: MagicMock())
    backend = selection._create_cinnamon_wayland_backend(
        model=MagicMock(), reason="native Wayland", **identity_services()
    )
    assert backend is not None
    assert type(backend.windows) is MuffinWindowService
    assert backend.capabilities.tracks_windows
    assert not backend.capabilities.supports_activate
