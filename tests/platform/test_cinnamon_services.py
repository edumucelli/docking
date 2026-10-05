"""Workspace eligibility, overlap modes and native applet failure semantics."""

from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import GLib

from docking.core.config import Config, HideMode
from docking.platform.backends.base import ActionResult, Rect, WorkspaceSnapshot
from docking.platform.backends.cinnamon.applets import CinnamonIdleService
from docking.platform.backends.cinnamon.picking import CinnamonWindowPickService
from docking.platform.backends.cinnamon.services import (
    CinnamonPreviewService,
    CinnamonVisibilityService,
    CinnamonWorkspaceService,
)
from docking.platform.backends.cinnamon.shell import CinnamonShellClient
from tests.platform.test_cinnamon_windows import _proxy, _row, _service


def workspace_state(client, rows, active="0"):
    client.rows = rows
    client.workspace_snapshots = tuple(
        WorkspaceSnapshot(str(i), i, f"Space {i}", str(i) == active) for i in range(2)
    )
    client.list_windows.return_value = rows


def test_workspace_filter_updates_running_lists_and_actions_with_sticky_windows():
    rows = (
        _row(1, workspace=0),
        _row(2, workspace=1),
        _row(3, workspace=1, sticky=True),
    )
    windows, client, model = _service(*rows)
    windows._config = Config(current_workspace_only=True)
    workspace_state(client, rows)
    windows.refresh()
    assert (
        model.update_running.call_args.kwargs["running"]["firefox.desktop"].count == 2
    )
    assert [
        w.id.value.split(":")[-1] for w in windows.list_windows("firefox.desktop")
    ] == ["1", "3"]
    assert len(windows.list_all_windows()) == 3
    workspace_state(client, rows, active="1")
    windows.refresh()
    assert [
        w.id.value.split(":")[-1] for w in windows.list_windows("firefox.desktop")
    ] == ["2", "3"]
    windows.close_all("firefox.desktop")
    assert [
        call.args[0].value.split(":")[-1]
        for call in client.window_action.call_args_list
    ] == ["2", "3"]
    windows._config.current_workspace_only = False
    windows.refresh()
    assert (
        model.update_running.call_args.kwargs["running"]["firefox.desktop"].count == 3
    )


def test_workspace_service_shares_refresh_and_cleans_up_watchers():
    windows, client, _model = _service()
    workspace_state(client, ())
    client.activate_workspace = MagicMock(return_value=ActionResult.OK)
    service = CinnamonWorkspaceService(windows=windows)
    callback = MagicMock()
    handle = service.watch_active_workspace(callback)
    service.start()
    service.start()
    windows.refresh()
    windows.refresh()
    callback.assert_called_once()
    workspace_state(client, (), active="1")
    windows.refresh()
    assert callback.call_count == 2
    assert service.active_workspace().id == "1"
    assert service.activate("missing") is ActionResult.NOT_FOUND
    assert service.activate("0") is ActionResult.OK
    client.last_query_failed = True
    assert service.activate("1") is ActionResult.FAILED
    service.unwatch_active_workspace(handle)
    service.stop()
    assert not windows._listeners


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (HideMode.NONE, False),
        (HideMode.AUTOHIDE, False),
        (HideMode.ALWAYS_ON_TOP, False),
        (HideMode.DODGE_ACTIVE, False),
        (HideMode.WINDOW_DODGE, True),
        (HideMode.INTELLIGENT, True),
        (HideMode.DODGE_MAXIMIZED, False),
    ],
)
def test_overlap_modes_distinguish_inactive_window_of_focused_app(mode, expected):
    rows = (
        _row(1, focused=True, workspace=0, pid=99, **{"frame-rect": [0, 0, 40, 40]}),
        _row(2, workspace=0, pid=99, **{"frame-rect": [0, 80, 40, 40]}),
    )
    windows, client, _model = _service(*rows)
    workspace_state(client, rows)
    callback = MagicMock()
    monitor = CinnamonVisibilityService(
        windows=windows, config=Config(hide_mode=mode.value)
    ).create_monitor(
        get_dock_rect=lambda: Rect(0, 100, 80, 20),
        on_change=callback,
    )
    monitor.start()
    assert monitor._hidden is expected
    if expected:
        callback.assert_called_once_with(True)
    monitor.stop()
    assert not windows._listeners


@pytest.mark.parametrize(
    "fields",
    [
        {"minimized": True},
        {"workspace": 1},
        {"visible": False},
        {"pid": os.getpid()},
        {"desktop-or-dock": True},
        {"frame-rect": [0, 0, -1, 2]},
    ],
)
def test_overlap_excludes_nonvisible_own_and_invalid_windows(fields):
    row = _row(1, workspace=0, focused=True, pid=99, **{"frame-rect": [0, 80, 40, 40]})
    row.update(fields)
    windows, client, _model = _service(row)
    workspace_state(client, (row,))
    monitor = CinnamonVisibilityService(
        windows=windows, config=Config(hide_mode=HideMode.WINDOW_DODGE.value)
    ).create_monitor(
        get_dock_rect=lambda: Rect(0, 100, 80, 20),
        on_change=MagicMock(),
    )
    monitor.evaluate_now()
    assert not monitor._hidden


def test_overlap_dialog_sticky_transition_and_failed_snapshot():
    rows = (
        _row(1, workspace=0, focused=True, pid=99, **{"frame-rect": [0, 0, 40, 40]}),
        _row(
            2,
            workspace=1,
            pid=99,
            sticky=True,
            dialog=True,
            **{"frame-rect": [0, 80, 40, 40]},
        ),
    )
    windows, client, _model = _service(*rows)
    workspace_state(client, rows)
    callback = MagicMock()
    config = Config(hide_mode=HideMode.DODGE_MAXIMIZED.value)
    monitor = CinnamonVisibilityService(windows=windows, config=config).create_monitor(
        get_dock_rect=lambda: Rect(0, 100, 80, 20),
        on_change=callback,
    )
    monitor.start()
    callback.assert_called_once_with(True)
    client.last_query_failed = True
    client.rows = ()
    monitor.evaluate_now()
    assert callback.call_count == 1
    client.last_query_failed = False
    monitor.evaluate_now()
    assert callback.call_args.args == (False,)


@pytest.mark.parametrize(
    ("show", "operation", "argument"),
    [
        (None, "toggle_desktop", "global.get_current_time()"),
        (True, "show_desktop", "global.get_current_time()"),
        (False, "unshow_desktop", ""),
    ],
)
def test_show_desktop_explicit_actions_do_not_guess_toggle_state(
    show, operation, argument
):
    proxy = _proxy("ok")
    assert CinnamonShellClient(proxy=proxy).show_desktop(show) is ActionResult.OK
    assert f".{operation}({argument})" in proxy.call_sync.call_args.args[1].unpack()[0]


@pytest.mark.parametrize(
    ("milliseconds", "seconds"),
    [(0, 0.0), (1501, 1.501), (-1, None), (True, None), ("1000", None)],
)
def test_idle_unit_conversion_and_invalid_response(milliseconds, seconds):
    proxy = SimpleNamespace(
        call_sync=MagicMock(
            return_value=SimpleNamespace(unpack=lambda: (milliseconds,))
        )
    )
    assert CinnamonIdleService(proxy=proxy).idle_seconds() == seconds
    proxy.call_sync.side_effect = GLib.Error("no owner")
    assert CinnamonIdleService(proxy=proxy).idle_seconds() is None


def test_preview_rejects_invalid_images_and_stale_targets():
    windows, _client, _model = _service(_row())
    windows.refresh()
    target = windows.list_all_windows()[0].id
    client = SimpleNamespace(for_window=MagicMock(return_value="not a png"))
    service = CinnamonPreviewService(client=client)
    assert service.capture(target, width=0, height=100) is None
    client.for_window.assert_not_called()
    assert service.capture(target, width=2000, height=100) is None
    assert service.capture(target, width=200, height=100) is None
    client.for_window.return_value = base64.b64encode(b"invalid png").decode()
    assert service.thumbnail(target, width=200, height=100) is None
    client.for_window.return_value = None
    assert service.capture(target, width=200, height=100) is None


def test_picker_revalidates_native_pid_and_termination_target():
    windows, client, _model = _service(_row())
    windows.refresh()
    target = windows.list_all_windows()[0].id
    client.for_window = MagicMock(return_value=99)
    client._action_result = CinnamonShellClient._action_result
    picker = CinnamonWindowPickService(windows=windows)
    assert picker.pid_for(target) == 99
    client.for_window.return_value = os.getpid()
    assert picker.pid_for(target) is None
    client.for_window.return_value = None
    assert picker.kill(target) is ActionResult.NOT_FOUND
    client.last_request_failed = True
    assert picker.kill(target) is ActionResult.FAILED
    client.last_request_failed = False
    client.for_window.return_value = "ok"
    assert picker.kill(target) is ActionResult.OK
    assert "w.kill()" in client.for_window.call_args.args[1]


def test_snapshot_metadata_and_features_are_probed_separately_from_windows():
    proxy = _proxy(
        {
            "windows": [_row()],
            "workspaces": [{"id": "0", "number": 1, "name": "First", "active": True}],
        }
    )
    client = CinnamonShellClient(proxy=proxy)
    assert client.list_windows()[0]["id"] == 1
    assert client.workspace_snapshots[0].active
    proxy.call_sync.return_value = SimpleNamespace(
        unpack=lambda: (False, json.dumps(None))
    )
    assert not client.list_windows()
    assert client.last_query_failed
    assert client.workspace_snapshots[0].name == "First"


@pytest.mark.parametrize("value", [None, [123, 456], "cinnamon:shell-1:1"])
def test_modal_selection_releases_timer_and_shell_resources(monkeypatch, value):
    from docking.platform.backends.cinnamon.selection import CinnamonSelection

    client = SimpleNamespace(
        bus_name=":1.10",
        _eval=MagicMock(side_effect=[True, {"done": True, "value": value}, True]),
    )
    loop = MagicMock()
    callbacks = []
    monkeypatch.setattr(GLib, "MainLoop", lambda: loop)
    monkeypatch.setattr(
        GLib, "timeout_add", lambda _delay, fn: callbacks.append(fn) or 42
    )
    remove = MagicMock()
    monkeypatch.setattr(GLib, "source_remove", remove)
    loop.run.side_effect = lambda: callbacks[0]()
    selector = CinnamonSelection(client=client)
    assert selector.select(pick_body="return [x,y];") == value
    assert selector._token is None and selector._loop is None
    remove.assert_called_once_with(42)
    assert "bus_watch_name" in client._eval.call_args_list[0].args[0]
    assert "s.dispose()" in client._eval.call_args_list[-1].args[0]


def test_modal_selection_fails_before_grabbing_input_without_client_identity():
    from docking.platform.backends.cinnamon.selection import CinnamonSelection

    client = SimpleNamespace(bus_name=None, _eval=MagicMock())
    selector = CinnamonSelection(client=client)
    assert selector.select(pick_body="return [x,y];") is None
    client._eval.assert_not_called()


def test_modal_selection_stop_releases_active_grab_and_nested_loop():
    from docking.platform.backends.cinnamon.selection import CinnamonSelection

    client = SimpleNamespace(_eval=MagicMock())
    selector = CinnamonSelection(client=client)
    selector._token = "owned"
    selector._loop = MagicMock()
    selector.stop()
    assert "finish(null)" in client._eval.call_args.args[0]
    selector._loop.quit.assert_called_once()


@pytest.mark.parametrize("layer_shell", [False, True])
@pytest.mark.parametrize("supported", [False, True])
def test_session_services_and_capabilities_follow_native_feature_probes(
    monkeypatch, layer_shell, supported
):
    from docking.platform.backends.cinnamon.session import (
        CinnamonShellSessionBackend,
        CinnamonWaylandSessionBackend,
    )
    from docking.platform.backends.cinnamon.shell import NATIVE_FEATURES
    from tests.platform.application_fakes import identity_services
    from tests.platform.test_cinnamon_wayland import _layer_shell, _model

    monkeypatch.setattr(
        "docking.platform.backends.wayland.session.WaylandProtocolRuntime.start",
        lambda _: False,
    )
    for loader in (
        "load_foreign_toplevel_protocol",
        "load_workspace_protocol",
        "load_portal_color_picker",
    ):
        monkeypatch.setattr(
            f"docking.platform.backends.wayland.session.{loader}", lambda: None
        )
    monkeypatch.setattr(CinnamonIdleService, "connect", lambda: None)
    client = CinnamonShellClient(proxy=_proxy({}))
    client.features = NATIVE_FEATURES if supported else frozenset()
    common = dict(model=_model(), **identity_services())
    backend = (
        CinnamonWaylandSessionBackend(
            layer_shell=_layer_shell(), shell_client=client, **common
        )
        if layer_shell
        else CinnamonShellSessionBackend(client=client, **common)
    )
    capabilities = backend.capabilities
    assert capabilities.supports_layer_shell is layer_shell
    assert capabilities.supports_current_workspace_filter is supported
    assert capabilities.supports_show_desktop is supported
    assert capabilities.supports_any_overlap is supported
    assert capabilities.supports_window_pick is supported
    assert capabilities.supports_screen_color_pick is supported
    assert not capabilities.supports_idle_time
    assert (backend.workspaces is not None) is supported
    assert isinstance(backend.previews, CinnamonPreviewService) is supported


def test_surface_accepts_new_shared_geometry_without_reusing_an_older_snapshot():
    from docking.platform.backends.cinnamon.shell import CinnamonShellSurfaceService
    from tests.platform.test_cinnamon_shell import _request

    client = SimpleNamespace(
        snapshot_revision=1,
        last_query_failed=False,
        rows=(),
        position_dock=MagicMock(return_value=(0, 600)),
    )
    surface = CinnamonShellSurfaceService(client=client)
    surface._request = _request()
    surface._apply_position()
    client.rows = ({"title": surface._title, "frame-rect": [10, 580, 400, 60]},)
    assert surface.get_surface_position() == (0, 600)
    client.snapshot_revision += 1
    assert surface.get_surface_position() == (10, 580)
    client.last_query_failed = True
    client.snapshot_revision += 1
    client.rows = ({"title": surface._title, "frame-rect": [20, 500, 400, 60]},)
    assert surface.get_surface_position() == (10, 580)
