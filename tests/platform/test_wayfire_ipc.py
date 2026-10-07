"""Tests for Wayfire IPC helpers and services."""

from __future__ import annotations

import json
import struct
from types import SimpleNamespace
from unittest.mock import MagicMock

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
    assert windows[0].workspace_id == "1"
    assert windows[0].can_minimize is True
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
        "docking.applets.windowkiller.state.kill_pid",
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
    client = FakeIpcClient({})
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
