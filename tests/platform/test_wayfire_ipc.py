"""Tests for Wayfire IPC helpers and services."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.platform.backends.wayland.wayfire_ipc as wayfire_ipc
from docking.core.items import DockItem
from docking.platform.backends.base import ActionResult, DisplayServer, WindowId
from docking.platform.backends.wayland.wayfire_ipc import (
    WayfireIpcClient,
    WayfireWindowPickService,
    WayfireWindowService,
    WayfireWorkspaceService,
    wayfire_socket_path,
)
from tests.platform.application_fakes import identity_services


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(
            return_value=[
                DockItem(desktop_id="Alacritty.desktop", wm_class="Alacritty"),
                DockItem(desktop_id="firefox.desktop", wm_class="firefox"),
            ]
        ),
        update_running=MagicMock(),
    )


class FakeWayfireSocket:
    def __init__(self, response: object) -> None:
        payload = json.dumps(response).encode("utf-8")
        self.response = struct.pack("I", len(payload)) + payload
        self.sent = b""
        self.connected_to: str | None = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        self.closed = True

    def settimeout(self, _timeout: float) -> None:
        return

    def connect(self, path: str) -> None:
        self.connected_to = path

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, size: int) -> bytes:
        chunk = self.response[:size]
        self.response = self.response[size:]
        return chunk


class FakeIpcClient:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data
        self.actions: list[tuple[str, dict]] = []
        self.requests: list[str] = []

    def request(self, method: str, data=None):
        self.requests.append(method)
        return self.data.get(method)

    def supports_method(self, method: str) -> bool:
        methods = self.data.get("list-methods")
        return not isinstance(methods, dict) or method in methods.get("methods", [])

    def action(self, method: str, data: dict) -> ActionResult:
        self.actions.append((method, data))
        return ActionResult.OK


class FakeGLib:
    SOURCE_CONTINUE = True

    def __init__(self) -> None:
        self.callbacks = []
        self.removed = []

    def timeout_add_seconds(self, interval: int, callback):
        self.callbacks.append((interval, callback))
        return len(self.callbacks)

    def source_remove(self, source_id: int) -> None:
        self.removed.append(source_id)


def test_wayfire_socket_path_from_environment():
    assert str(wayfire_socket_path({"WAYFIRE_SOCKET": "/run/user/1000/wf.sock"})) == (
        "/run/user/1000/wf.sock"
    )


def test_wayfire_socket_path_missing_environment():
    assert wayfire_socket_path({}) is None


@pytest.mark.parametrize(
    "active, minimized, visible",
    [(True, False, None), (False, False, None), (False, True, False)],
)
def test_wayfire_snapshot_visibility_does_not_guess_inactive_workspaces(
    active, minimized, visible
):
    from docking.platform.backends.base import Rect

    record = wayfire_ipc.WayfireWindowRecord(
        id=7,
        title="Terminal",
        app_id="Alacritty",
        application_match=None,
        active=active,
        minimized=minimized,
        fullscreen=False,
        geometry=Rect(0, 0, 800, 600),
        workspace_id="1",
        pid=1234,
    )
    service = WayfireWindowService(
        model=_model(), **identity_services(), client=MagicMock()
    )
    snapshot = service._snapshot_for(record)
    assert snapshot.visible is visible
    assert snapshot.pid == 1234


@pytest.mark.parametrize("x, visible", [(0, True), (-800, False), (800, False)])
def test_wayfire_snapshot_visibility_uses_its_own_output_viewport(x, visible):
    from docking.platform.backends.base import Rect

    record = wayfire_ipc.WayfireWindowRecord(
        id=7,
        title="Terminal",
        app_id="Alacritty",
        application_match=None,
        active=True,
        minimized=False,
        fullscreen=False,
        geometry=Rect(x, 0, 800, 600),
        workspace_id="1",
        pid=1234,
        output_id=2,
    )
    service = WayfireWindowService(
        model=_model(), **identity_services(), client=MagicMock()
    )
    # A negative global output origin is valid; view geometry is output-relative.
    service._outputs = {
        2: {"id": 2, "geometry": {"x": -800, "y": 0, "width": 800, "height": 600}}
    }
    assert service._snapshot_for(record).visible is visible


def test_wayfire_ipc_client_uses_length_prefixed_json():
    sockets: list[FakeWayfireSocket] = []

    def socket_factory(_family: int, _type: int):
        sock = FakeWayfireSocket({"result": "ok"})
        sockets.append(sock)
        return sock

    client = WayfireIpcClient(
        socket_path="/run/user/1000/wayfire-wayland-1-.socket",
        socket_factory=socket_factory,
    )

    assert client.request("window-rules/list-views") == {"result": "ok"}
    sock = sockets[0]
    assert sock.connected_to == "/run/user/1000/wayfire-wayland-1-.socket"
    request_len = struct.unpack("I", sock.sent[:4])[0]
    request = json.loads(sock.sent[4 : 4 + request_len])
    assert request == {"method": "window-rules/list-views", "data": {}}
    assert sock.closed is True


def test_wayfire_payload_with_pid_flows_through_real_matcher_to_model():
    model = _model()
    client = FakeIpcClient(
        {
            "window-rules/list-views": [
                {
                    "id": 7,
                    "type": "toplevel",
                    "role": "toplevel",
                    "mapped": True,
                    "title": "Terminal",
                    "app-id": "Alacritty",
                    "activated": True,
                    "minimized": False,
                    "fullscreen": False,
                    "wset-index": 1,
                    "output-id": 1,
                    "geometry": {"x": 10, "y": 20, "width": 800, "height": 600},
                    "pid": 1234,
                },
                {
                    "id": 8,
                    "type": "panel",
                    "role": "desktop-environment",
                    "app-id": "panel",
                },
            ],
            "window-rules/get-focused-view": {"result": "ok", "info": {"id": 7}},
            "window-rules/list-outputs": [
                {
                    "id": 1,
                    "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
                    "workspace": {"x": 0, "y": 0, "grid_width": 2, "grid_height": 2},
                    "wset-index": 1,
                }
            ],
        }
    )
    services = identity_services()
    service = WayfireWindowService(
        model=model,
        **services,
        client=client,
    )

    service.start()

    windows = service.list_windows("Alacritty.desktop")
    assert len(windows) == 1
    assert windows[0].id == WindowId(DisplayServer.WAYLAND, "7")
    assert windows[0].title == "Terminal"
    assert windows[0].active is True
    assert windows[0].geometry is not None
    assert windows[0].workspace_id == "1:0,0"
    assert windows[0].can_minimize is True
    assert windows[0].pid == 1234
    assert windows[0].visible is True
    application_match = service._records[7].application_match
    assert application_match is not None
    assert application_match.desktop_id == "Alacritty.desktop"
    assert application_match.application is services["application_registry"].get(
        "Alacritty.desktop"
    )
    model.update_running.assert_called()

    assert service.activate(windows[0].id) is ActionResult.OK
    assert service.close(windows[0].id) is ActionResult.OK
    assert client.actions == [
        ("window-rules/focus-view", {"id": 7}),
        ("window-rules/close-view", {"id": 7}),
    ]


def test_wayfire_window_service_polls_for_late_window_changes(monkeypatch):
    fake_glib = FakeGLib()
    monkeypatch.setattr(wayfire_ipc, "GLib", fake_glib)
    model = _model()
    client = FakeIpcClient(
        {
            "window-rules/list-views": [
                {
                    "id": 7,
                    "type": "toplevel",
                    "role": "toplevel",
                    "mapped": True,
                    "title": "Terminal",
                    "app-id": "Alacritty",
                },
            ],
            "window-rules/get-focused-view": {"result": "ok", "info": {"id": 7}},
        }
    )
    service = WayfireWindowService(
        model=model,
        **identity_services(),
        client=client,
    )

    service.start()

    assert fake_glib.callbacks
    assert fake_glib.callbacks[0][0] == wayfire_ipc.WAYFIRE_WINDOW_POLL_SECONDS
    client.data["window-rules/list-views"] = [
        {
            "id": 9,
            "type": "toplevel",
            "role": "toplevel",
            "mapped": True,
            "title": "Browser",
            "app-id": "firefox",
        },
    ]

    assert fake_glib.callbacks[0][1]() is True
    running = model.update_running.call_args.kwargs["running"]
    assert set(running) == {"firefox.desktop"}

    service.stop()

    assert fake_glib.removed == [1]


def test_wayfire_workspace_service_lists_grid_cells_and_marks_active():
    client = FakeIpcClient(
        {
            "window-rules/list-outputs": [
                {
                    "id": 1,
                    "name": "X11-1",
                    "workspace": {
                        "grid_width": 2,
                        "grid_height": 2,
                        "x": 1,
                        "y": 0,
                    },
                }
            ],
            "window-rules/get-focused-output": {"result": "ok", "info": {"id": 1}},
        }
    )
    service = WayfireWorkspaceService(client=client)

    service.start()

    workspaces = service.list_workspaces()
    assert [workspace.number for workspace in workspaces] == [1, 2, 3, 4]
    assert [workspace.active for workspace in workspaces] == [
        False,
        True,
        False,
        False,
    ]
    assert service.active_workspace() == workspaces[1]
    result = service.activate(workspaces[0].id)
    assert result is ActionResult.OK
    assert (
        "vswitch/set-workspace",
        {"output-id": 1, "x": 0, "y": 0},
    ) in client.actions


def test_wayfire_window_picker_uses_geometry_and_pid(monkeypatch):
    client = FakeIpcClient(
        {
            "window-rules/list-views": [
                {
                    "id": 7,
                    "type": "toplevel",
                    "role": "toplevel",
                    "mapped": True,
                    "title": "Bottom",
                    "app-id": "Alacritty",
                    "geometry": {"x": 0, "y": 0, "width": 200, "height": 200},
                    "pid": 111,
                },
                {
                    "id": 8,
                    "type": "toplevel",
                    "role": "toplevel",
                    "mapped": True,
                    "title": "Top",
                    "app-id": "firefox",
                    "geometry": {"x": 50, "y": 50, "width": 200, "height": 200},
                    "pid": 222,
                },
            ],
            "window-rules/get-focused-view": {"result": "ok", "info": {"id": 8}},
        }
    )
    killed = MagicMock(return_value=True)
    monkeypatch.setattr(
        "docking.platform.backends.unix.processes.kill_pid",
        killed,
    )
    service = WayfireWindowPickService(client=client)

    target = service.pick_window_at(x=75, y=75)

    assert target is not None
    assert target.id == WindowId(DisplayServer.WAYLAND, "8")
    assert target.title == "Top"
    assert service.pid_for(target.id) == 222
    assert service.kill(target.id) is ActionResult.OK
    killed.assert_called_once_with(pid=222)


def test_wayfire_window_service_minimize_all_and_toggle_focus():
    model = _model()
    client = FakeIpcClient(
        {
            "window-rules/list-views": [
                {
                    "id": 7,
                    "type": "toplevel",
                    "role": "toplevel",
                    "mapped": True,
                    "title": "Terminal",
                    "app-id": "Alacritty",
                    "activated": True,
                },
            ],
            "window-rules/get-focused-view": {"result": "ok", "info": {"id": 7}},
        }
    )
    service = WayfireWindowService(
        model=model,
        **identity_services(),
        client=client,
    )

    service.start()

    result = service.minimize_all("Alacritty.desktop")
    assert result is ActionResult.OK
    assert (
        "wm-actions/set-minimized",
        {"view_id": 7, "state": True},
    ) in client.actions

    client.actions.clear()
    result = service.toggle_focus("Alacritty.desktop")
    assert result is ActionResult.OK
    assert (
        "wm-actions/set-minimized",
        {"view_id": 7, "state": True},
    ) in client.actions


def test_wayfire_desktop_action_service_show_desktop():
    client = FakeIpcClient({"window-rules/get-focused-output": {"info": {"id": 1}}})
    service = wayfire_ipc.WayfireDesktopActionService(client=client)

    service.start()

    # Toggle None: should call wm-actions/toggle_showdesktop (state → True)
    result = service.show_desktop(None)
    assert result is ActionResult.OK
    assert (
        "wm-actions/toggle_showdesktop",
        {"output-id": 1},
    ) in client.actions

    # Explicit True when already True: no-op (no duplicate toggle)
    client.actions.clear()
    result = service.show_desktop(True)
    assert result is ActionResult.OK
    assert len(client.actions) == 0

    # Explicit False changes state and toggles (state → False)
    client.actions.clear()
    result = service.show_desktop(False)
    assert result is ActionResult.OK
    assert (
        "wm-actions/toggle_showdesktop",
        {"output-id": 1},
    ) in client.actions

    # Explicit False when already False: no-op
    client.actions.clear()
    result = service.show_desktop(False)
    assert result is ActionResult.OK
    assert len(client.actions) == 0

    service.stop()


def test_wayfire_visibility_service_creates_monitor():
    mock_config = MagicMock()
    type(mock_config).hide_mode_enum = "dodge-active"
    client = FakeIpcClient(
        {
            "window-rules/list-views": [
                {
                    "id": 7,
                    "type": "toplevel",
                    "role": "toplevel",
                    "mapped": True,
                    "title": "Terminal",
                    "app-id": "Alacritty",
                    "activated": True,
                    "minimized": False,
                    "fullscreen": False,
                    "geometry": {"x": 0, "y": 0, "width": 800, "height": 600},
                },
            ],
            "window-rules/get-focused-view": {"result": "ok", "info": {"id": 7}},
        }
    )
    service = wayfire_ipc.WayfireVisibilityService(
        client=client,
        config=mock_config,
    )

    dock_rect = wayfire_ipc.Rect(x=0, y=550, width=1920, height=50)
    changes: list[bool] = []

    monitor = service.create_monitor(
        get_dock_rect=lambda: dock_rect,
        on_change=changes.append,
    )

    assert monitor is not None
    # Force immediate evaluation - window at y=0,h=600 overlaps dock at y=550,h=50
    monitor.evaluate_now()
    assert changes == [True]

    monitor.stop()


def test_wayfire_visibility_service_no_overlap_with_no_windows():
    mock_config = MagicMock()
    type(mock_config).hide_mode_enum = "window-dodge"
    client = FakeIpcClient(
        {
            "window-rules/list-views": [],
            "window-rules/get-focused-view": {"result": "ok", "info": {}},
        }
    )
    service = wayfire_ipc.WayfireVisibilityService(
        client=client,
        config=mock_config,
    )

    dock_rect = wayfire_ipc.Rect(x=0, y=550, width=1920, height=50)
    changes: list[bool] = []

    monitor = service.create_monitor(
        get_dock_rect=lambda: dock_rect,
        on_change=changes.append,
    )

    assert monitor is not None
    monitor.evaluate_now()
    # No windows, no overlap
    assert changes == []

    monitor.stop()


def test_wayfire_event_watcher_dispatches_events_to_handler():
    """Simulate an event stream and verify the handler updates records."""
    model = _model()
    service = WayfireWindowService(
        model=model,
        **identity_services(),
        client=FakeIpcClient({}),
    )

    # Simulate view-mapped event
    service._on_event(
        "view-mapped",
        {
            "id": 7,
            "type": "toplevel",
            "role": "toplevel",
            "mapped": True,
            "title": "Terminal",
            "app-id": "Alacritty",
            "activated": False,
        },
    )
    assert 7 in service._records
    assert service._records[7].title == "Terminal"
    assert service._records[7].active is False

    # view-focused marks only this view active
    service._on_event(
        "view-focused",
        {
            "id": 7,
            "type": "toplevel",
            "role": "toplevel",
            "mapped": True,
            "title": "Terminal",
            "app-id": "Alacritty",
            "activated": True,
        },
    )
    assert service._records[7].active is True

    # Add a second view
    service._on_event(
        "view-mapped",
        {
            "id": 8,
            "type": "toplevel",
            "role": "toplevel",
            "mapped": True,
            "title": "Browser",
            "app-id": "firefox",
            "activated": False,
        },
    )
    assert 8 in service._records

    # Focus the second view: first becomes inactive
    service._on_event(
        "view-focused",
        {
            "id": 8,
            "type": "toplevel",
            "role": "toplevel",
            "mapped": True,
            "title": "Browser",
            "app-id": "firefox",
            "activated": True,
        },
    )
    assert service._records[8].active is True
    assert service._records[7].active is False

    # view-unmapped removes the view
    service._on_event("view-unmapped", {"id": 7})
    assert 7 not in service._records
    assert 8 in service._records

    service.stop()


def test_wayfire_window_service_falls_back_to_polling_without_watcher(monkeypatch):
    """Without a watcher, the service starts a GLib polling timer."""
    fake_glib = FakeGLib()
    monkeypatch.setattr(wayfire_ipc, "GLib", fake_glib)
    model = _model()
    client = FakeIpcClient(
        {
            "window-rules/list-views": [
                {
                    "id": 7,
                    "type": "toplevel",
                    "role": "toplevel",
                    "mapped": True,
                    "title": "Terminal",
                    "app-id": "Alacritty",
                },
            ],
            "window-rules/get-focused-view": {"result": "ok", "info": {"id": 7}},
        }
    )
    service = WayfireWindowService(
        model=model,
        **identity_services(),
        client=client,
        watcher=None,
    )
    service.start()
    assert fake_glib.callbacks
    assert fake_glib.callbacks[0][0] == wayfire_ipc.WAYFIRE_WINDOW_POLL_SECONDS
    service.stop()


def _output(output_id=2, x=800, y=-100, active_x=1):
    return {
        "id": output_id,
        "name": f"HEADLESS-{output_id}",
        "wset-index": 9,
        "geometry": {"x": x, "y": y, "width": 800, "height": 600},
        "workspace": {"x": active_x, "y": 0, "grid_width": 3, "grid_height": 2},
    }


def _view(view_id=7, x=10, y=20, **extra):
    return {
        "id": view_id,
        "type": "toplevel",
        "mapped": True,
        "app-id": "Alacritty",
        "title": "Terminal",
        "output-id": 2,
        "wset-index": 9,
        "geometry": {"x": x, "y": y, "width": 200, "height": 150},
        "minimized": False,
        "fullscreen": False,
        "pid": 1234,
        **extra,
    }


def _window_client(views, outputs=None, focused_id=None, methods=None):
    data = {
        "window-rules/list-views": views,
        "window-rules/list-outputs": outputs if outputs is not None else [_output()],
        "window-rules/get-focused-view": {"info": {"id": focused_id}}
        if focused_id
        else {"info": None},
        "window-rules/get-focused-output": {"info": {"id": 2}},
    }
    if methods is not None:
        data["list-methods"] = {"methods": methods}
    return FakeIpcClient(data)


@pytest.mark.parametrize("origin_x", [-800, 800])
def test_wayfire_windows_and_picker_use_global_output_coordinates(origin_x):
    from docking.platform.backends.base import Rect

    client = _window_client([_view()], [_output(x=origin_x)])
    windows = WayfireWindowService(model=_model(), **identity_services(), client=client)
    windows._refresh()
    snapshot = windows.list_all_windows()[0]
    assert snapshot.geometry == Rect(origin_x + 10, -80, 200, 150)
    assert snapshot.workspace_id == "2:1,0"
    assert snapshot.visible is True
    assert snapshot.on_current_workspace is True
    picker = WayfireWindowPickService(client=client)
    assert picker.pick_window_at(x=origin_x + 20, y=-60).id == snapshot.id
    assert picker.pick_window_at(x=20, y=-60) is None


@pytest.mark.parametrize(
    "relative_x, workspace_id, visible",
    [
        (-800, "2:0,0", False),
        (10, "2:1,0", True),
        (800, "2:2,0", False),
    ],
)
def test_wayfire_window_workspace_identity_matches_grid_service(
    relative_x, workspace_id, visible
):
    client = _window_client([_view(x=relative_x)])
    windows = WayfireWindowService(model=_model(), **identity_services(), client=client)
    windows._refresh()
    snapshot = windows.list_all_windows()[0]
    assert snapshot.workspace_id == workspace_id
    assert snapshot.visible is visible
    assert snapshot.on_current_workspace is visible
    workspaces = WayfireWorkspaceService(client=client)
    assert workspace_id in {workspace.id for workspace in workspaces.list_workspaces()}


@pytest.mark.parametrize("service", ["pick", "dodge"])
def test_wayfire_hidden_workspace_window_does_not_leak_onto_neighbor_output(service):
    # Window on output 2's next cell numerically overlaps output 3 in global
    # coordinates. Its own viewport determines visibility, not the other output.
    client = _window_client(
        [_view(x=800, y=0, activated=True)],
        [
            _output(x=0, y=0),
            _output(output_id=3, x=800, y=0),
        ],
        focused_id=7,
    )
    if service == "pick":
        assert (
            WayfireWindowPickService(client=client).pick_window_at(x=850, y=50) is None
        )
    else:
        monitor = wayfire_ipc.WayfireVisibilityMonitor(
            client=client,
            config=SimpleNamespace(hide_mode_enum="dodge-active"),
            get_dock_rect=lambda: wayfire_ipc.Rect(800, 0, 800, 50),
            on_change=MagicMock(),
        )
        assert monitor._evaluate() is False


@pytest.mark.parametrize("mode", ["dodge-active", "window-dodge", "dodge-maximized"])
def test_wayfire_dodge_uses_global_coordinates_and_maximized_facts(mode):
    client = _window_client(
        [_view(y=450, activated=True, **{"tiled-edges": 15})], focused_id=7
    )
    monitor = wayfire_ipc.WayfireVisibilityMonitor(
        client=client,
        config=SimpleNamespace(hide_mode_enum=mode),
        get_dock_rect=lambda: wayfire_ipc.Rect(800, 450, 800, 50),
        on_change=MagicMock(),
    )
    assert monitor._evaluate() is True
    monitor._get_dock_rect = lambda: wayfire_ipc.Rect(0, 450, 800, 50)
    assert monitor._evaluate() is False


def test_wayfire_current_workspace_scope_preserves_minimized_and_sticky_windows():
    client = _window_client(
        [
            _view(view_id=1),
            _view(view_id=2, x=800),
            _view(view_id=3, minimized=True),
            _view(view_id=4, sticky=True),
        ]
    )
    model = _model()
    service = WayfireWindowService(
        model=model,
        **identity_services(),
        client=client,
        config=SimpleNamespace(current_workspace_only=True),
    )
    service._refresh()
    assert [
        window.id.value for window in service.list_windows("Alacritty.desktop")
    ] == ["1", "3", "4"]
    assert (
        len(
            model.update_running.call_args.kwargs["running"][
                "Alacritty.desktop"
            ].windows
        )
        == 3
    )
    assert service.list_all_windows()[2].visible is False
    assert service.list_all_windows()[2].on_current_workspace is True
    assert service.list_all_windows()[3].sticky is True


def test_wayfire_detached_workspace_set_is_hidden_even_at_visible_coordinates():
    client = _window_client([_view(**{"wset-index": 8})])
    service = WayfireWindowService(model=_model(), **identity_services(), client=client)
    service._refresh()
    snapshot = service.list_all_windows()[0]
    assert snapshot.visible is False
    assert snapshot.on_current_workspace is False
    assert snapshot.workspace_id is None


def test_wayfire_pluginless_session_keeps_tracking_but_rejects_optional_actions():
    client = _window_client(
        [_view(activated=True)],
        focused_id=7,
        methods=[
            "window-rules/list-views",
            "window-rules/list-outputs",
            "window-rules/focus-view",
        ],
    )
    service = WayfireWindowService(model=_model(), **identity_services(), client=client)
    service._refresh()
    assert service.list_all_windows()[0].can_minimize is False
    assert service.minimize_all("Alacritty.desktop") is ActionResult.UNSUPPORTED
    assert service.toggle_focus("Alacritty.desktop") is ActionResult.UNSUPPORTED
    workspaces = WayfireWorkspaceService(client=client)
    workspaces._refresh()
    assert workspaces.list_workspaces()
    assert workspaces.activate("2:0,0") is ActionResult.UNSUPPORTED
    assert client.actions == []


def test_wayfire_cycles_all_three_windows_and_uses_mru_for_activation():
    views = [
        _view(view_id=1),
        _view(view_id=2, **{"last-focus-timestamp": 99}),
        _view(view_id=3),
    ]
    client = _window_client(views)
    service = WayfireWindowService(model=_model(), **identity_services(), client=client)
    service._refresh()
    assert service.activate_most_recent("Alacritty.desktop") is ActionResult.OK
    assert client.actions[-1] == ("window-rules/focus-view", {"id": 2})
    for active_id, next_id in [(1, 2), (2, 3), (3, 1)]:
        client.data["window-rules/get-focused-view"] = {"info": {"id": active_id}}
        service._refresh()
        assert service.cycle("Alacritty.desktop") is ActionResult.OK
        assert client.actions[-1] == ("window-rules/focus-view", {"id": next_id})


def test_wayfire_close_focused_does_not_close_background_app():
    client = _window_client([_view()], focused_id=99)
    service = WayfireWindowService(model=_model(), **identity_services(), client=client)
    service._refresh()
    assert service.close_focused("Alacritty.desktop") is ActionResult.NOT_FOUND
    assert client.actions == []


def test_wayfire_null_focus_event_clears_active_app_and_output_events_refresh():
    client = _window_client([_view()], focused_id=7)
    service = WayfireWindowService(model=_model(), **identity_services(), client=client)
    service._refresh()
    service._on_event("view-focused", {})
    assert service.list_all_windows()[0].active is False
    client.data["window-rules/list-outputs"][0]["workspace"]["x"] = 2
    service._on_event("wset-workspace-changed", {})
    assert service.list_all_windows()[0].workspace_id == "2:2,0"


def test_wayfire_workspace_watch_notifies_external_switch_without_manual_query(
    monkeypatch,
):
    glib = FakeGLib()
    monkeypatch.setattr(wayfire_ipc, "GLib", glib)
    client = _window_client([])
    service = WayfireWorkspaceService(client=client)
    service.start()
    changed = MagicMock()
    service.watch_active_workspace(changed)
    client.data["window-rules/list-outputs"][0]["workspace"]["x"] = 2
    assert glib.callbacks[0][1]() is True
    changed.assert_called_once()
    service.stop()
    assert glib.removed == [1]


def test_wayfire_show_desktop_uses_focused_output_and_tracks_outputs_separately():
    client = _window_client([])
    service = wayfire_ipc.WayfireDesktopActionService(client=client)
    assert service.show_desktop(True) is ActionResult.OK
    assert client.actions[-1] == ("wm-actions/toggle_showdesktop", {"output-id": 2})
    client.data["window-rules/get-focused-output"] = {"info": {"id": 3}}
    assert service.show_desktop(True) is ActionResult.OK
    assert client.actions[-1] == ("wm-actions/toggle_showdesktop", {"output-id": 3})
    assert len(client.actions) == 2


@pytest.mark.parametrize(
    "error, expected",
    [
        ("No such method found!", ActionResult.UNSUPPORTED),
        ("no such view", ActionResult.NOT_FOUND),
        ("invalid geometry", ActionResult.FAILED),
    ],
)
def test_wayfire_ipc_action_reports_errors_without_claiming_missing_windows(
    error, expected
):
    client = WayfireIpcClient(socket_path="unused")
    client.request = MagicMock(side_effect=[{"methods": ["action"]}, {"error": error}])
    assert client.action("action", {}) is expected


def test_wayfire_ipc_missing_plugin_is_detected_without_executing_action():
    client = WayfireIpcClient(socket_path="unused")
    client.request = MagicMock(return_value={"methods": ["window-rules/list-views"]})
    assert (
        client.action("wm-actions/set-minimized", {"view_id": 7, "state": True})
        is ActionResult.UNSUPPORTED
    )
    assert client.supports_method("window-rules/list-views") is True
    assert client.supports_method("vswitch/set-workspace") is False
    client.request.assert_called_once_with("list-methods")


def test_wayfire_event_frames_survive_split_headers_and_payload_timeouts(monkeypatch):
    monkeypatch.setattr(wayfire_ipc, "GLib", None)
    data = json.dumps({"event": "view-focused", "view": None}).encode()
    frame = struct.pack("I", len(data)) + data
    sock = MagicMock()
    sock.recv.side_effect = [
        frame[:2],
        TimeoutError(),
        frame[2:8],
        TimeoutError(),
        frame[8:],
        b"",
    ]
    events = []
    watcher = wayfire_ipc.WayfireEventWatcher(
        socket_path="unused", on_event=lambda *args: events.append(args)
    )
    watcher._sock = sock
    watcher._running = True
    watcher._read_loop()
    assert watcher.running is False
    assert watcher._drain_queue() is False
    assert events == [("view-focused", {})]
    sock.close.assert_called_once()


@pytest.mark.parametrize("tail", [b"", b"\x01", struct.pack("I", 20) + b"{}"])
def test_wayfire_event_reader_handles_eof_in_partial_frames(monkeypatch, tail):
    monkeypatch.setattr(wayfire_ipc, "GLib", None)
    sock = MagicMock()
    sock.recv.side_effect = [tail, b""] if tail else [b""]
    watcher = wayfire_ipc.WayfireEventWatcher(
        socket_path="unused", on_event=MagicMock()
    )
    watcher._sock = sock
    watcher._running = True
    watcher._read_loop()
    assert watcher.running is False
    sock.close.assert_called_once()


def test_wayfire_watcher_failed_handshake_closes_socket():
    sock = MagicMock()
    sock.connect.side_effect = OSError("unavailable")
    watcher = wayfire_ipc.WayfireEventWatcher(
        socket_path="unused", on_event=MagicMock(), socket_factory=lambda *_args: sock
    )
    assert watcher.start() is False
    sock.close.assert_called_once()


def test_wayfire_loader_requires_real_snapshot_even_when_ipc_socket_exists(
    monkeypatch, tmp_path
):
    path = tmp_path / "socket"
    path.touch()
    monkeypatch.setattr(wayfire_ipc, "wayfire_socket_path", lambda: path)
    monkeypatch.setattr(
        WayfireIpcClient, "request", lambda *_args: {"error": "No such method found!"}
    )
    assert (
        wayfire_ipc.load_wayfire_window_service(model=_model(), **identity_services())
        is None
    )
    assert wayfire_ipc.load_wayfire_window_pick_service() is None
    assert wayfire_ipc.load_wayfire_workspace_service() is None
    assert wayfire_ipc.load_wayfire_visibility_service(config=SimpleNamespace()) is None


def test_wayfire_pick_and_overlap_respect_output_clipping_for_straddling_view():
    client = _window_client([_view(x=-100, y=0, activated=True)], [_output(x=800, y=0)])
    picker = WayfireWindowPickService(client=client)
    assert picker.pick_window_at(x=750, y=50) is None
    assert picker.pick_window_at(x=850, y=50) is not None
    monitor = wayfire_ipc.WayfireVisibilityMonitor(
        client=client,
        config=SimpleNamespace(hide_mode_enum="dodge-active"),
        get_dock_rect=lambda: wayfire_ipc.Rect(700, 0, 100, 50),
        on_change=MagicMock(),
    )
    assert monitor._evaluate() is False


def test_wayfire_focus_on_panel_clears_toplevel_focus():
    client = _window_client([_view()], focused_id=7)
    service = WayfireWindowService(model=_model(), **identity_services(), client=client)
    service._refresh()
    service._on_event(
        "view-focused", {"id": 99, "type": "panel", "role": "desktop-environment"}
    )
    assert service.list_all_windows()[0].active is False


def test_wayfire_picker_selects_raised_older_window_instead_of_newest_allocation():
    client = _window_client([_view(view_id=7), _view(view_id=8)], focused_id=7)
    picker = WayfireWindowPickService(client=client)
    assert picker.pick_window_at(x=820, y=-60).id.value == "7"


def test_wayfire_poll_restarts_disconnected_events_then_reconciles_snapshot(
    monkeypatch,
):
    monkeypatch.setattr(wayfire_ipc, "GLib", None)
    client = _window_client([_view()])
    watcher = MagicMock()
    watcher.start.return_value = True
    service = WayfireWindowService(
        model=_model(), **identity_services(), client=client, watcher=watcher
    )
    service.start()
    watcher.start.reset_mock()
    watcher.running = False
    assert service._poll() is True
    watcher.start.assert_called_once()
    assert len(service.list_all_windows()) == 1


def test_wayfire_initially_rejected_events_keep_polling_without_repeated_handshakes(
    monkeypatch,
):
    monkeypatch.setattr(wayfire_ipc, "GLib", None)
    client = _window_client([_view()])
    watcher = MagicMock(running=False)
    watcher.start.return_value = False
    service = WayfireWindowService(
        model=_model(), **identity_services(), client=client, watcher=watcher
    )
    service.start()
    client.data["window-rules/list-views"] = [_view(view_id=8)]
    for _ in range(10):
        assert service._poll() is True
    watcher.start.assert_called_once()
    assert service.list_all_windows()[0].id.value == "8"


def test_wayfire_failed_reconnect_waits_before_trying_again(monkeypatch):
    monkeypatch.setattr(wayfire_ipc, "GLib", None)
    clock = [100.0]
    monkeypatch.setattr(wayfire_ipc.time, "monotonic", lambda: clock[0])
    watcher = MagicMock(running=False)
    watcher.start.side_effect = [True, False, True]
    service = WayfireWindowService(
        model=_model(),
        **identity_services(),
        client=_window_client([_view()]),
        watcher=watcher,
    )
    service.start()
    service._poll()
    for _ in range(10):
        clock[0] += 2
        service._poll()
    assert watcher.start.call_count == 2
    clock[0] = 161.0
    service._poll()
    assert watcher.start.call_count == 3
    assert len(service.list_all_windows()) == 1


@pytest.mark.parametrize(
    "unknown", ["missing-output", "missing-geometry", "failed-query"]
)
def test_wayfire_unknown_workspace_state_retains_running_indicators(unknown):
    view, output = _view(), _output()
    if unknown == "missing-output":
        view.pop("output-id")
    elif unknown == "missing-geometry":
        output.pop("geometry")
    client = _window_client([view], [output])
    if unknown == "failed-query":
        request = client.request

        def unavailable(method, data=None):
            if method == "window-rules/list-outputs":
                raise OSError("Output query temporarily unavailable")
            return request(method, data)

        client.request = unavailable
    model = _model()
    service = WayfireWindowService(
        model=model,
        **identity_services(),
        client=client,
        config=SimpleNamespace(current_workspace_only=True),
    )
    service._refresh()
    windows = service.list_windows("Alacritty.desktop")
    assert len(windows) == 1
    assert windows[0].on_current_workspace is None
    assert "Alacritty.desktop" in model.update_running.call_args.kwargs["running"]
    assert service.close_all("Alacritty.desktop").succeeded


def test_wayfire_maximized_state_distinguishes_partial_tiling_from_all_edges():
    client = _window_client(
        [
            _view(view_id=7, **{"tiled-edges": 15}),
            _view(view_id=8, **{"tiled-edges": 3}),
        ]
    )
    service = WayfireWindowService(model=_model(), **identity_services(), client=client)
    service._refresh()
    assert [window.maximized for window in service.list_all_windows()] == [True, False]


@pytest.mark.parametrize("focused_id", [8, None])
def test_wayfire_keyboard_focus_overrides_stale_activation_on_another_output(
    focused_id,
):
    client = _window_client([_view(activated=True)], focused_id=focused_id)
    service = WayfireWindowService(model=_model(), **identity_services(), client=client)
    service._refresh()
    assert service.list_all_windows()[0].active is False
