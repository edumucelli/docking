"""Tests for Hyprland IPC helpers and window service."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from docking.platform.backends.base import ActionResult, DisplayServer, WindowId
from docking.platform.backends.wayland.hyprland_ipc import (
    HyprlandEvent,
    HyprlandIpcClient,
    HyprlandSocketPaths,
    HyprlandWindowService,
    hyprland_socket_paths,
    parse_hyprland_event,
)
from tests.platform.application_fakes import identity_services


def _model() -> SimpleNamespace:
    return SimpleNamespace(
        visible_items=MagicMock(
            return_value=[
                SimpleNamespace(desktop_id="Alacritty.desktop", wm_class="Alacritty"),
                SimpleNamespace(desktop_id="firefox.desktop", wm_class="firefox"),
            ]
        ),
        update_running=MagicMock(),
    )


class FakeCommandSocket:
    def __init__(self, response: object) -> None:
        self.response = json.dumps(response).encode("utf-8")
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

    def shutdown(self, _how: int) -> None:
        return

    def recv(self, _size: int) -> bytes:
        data = self.response
        self.response = b""
        return data


class FakeIpcClient:
    def __init__(self, snapshots: list[tuple[list[dict], dict]]) -> None:
        self.snapshots = snapshots
        self.query_index = 0
        self.dispatched: list[str] = []

    def query_json(self, command: str):
        clients, active = self.snapshots[self.query_index // 2]
        self.query_index += 1
        return clients if command == "clients" else active

    def dispatch(self, command: str) -> ActionResult:
        self.dispatched.append(command)
        return ActionResult.OK


class FakeEventStream:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def emit(self, name: str, data: str = "") -> None:
        self.callback(HyprlandEvent(name=name, data=data))


def test_hyprland_socket_paths_from_environment():
    paths = hyprland_socket_paths(
        {
            "XDG_RUNTIME_DIR": "/run/user/1000",
            "HYPRLAND_INSTANCE_SIGNATURE": "abc123",
        }
    )

    assert paths == HyprlandSocketPaths(
        command=Path("/run/user/1000/hypr/abc123/.socket.sock"),
        events=Path("/run/user/1000/hypr/abc123/.socket2.sock"),
    )


def test_hyprland_socket_paths_missing_environment():
    assert hyprland_socket_paths({}) is None


def test_parse_hyprland_event_line():
    event = parse_hyprland_event("openwindow>>0xabc,1,Alacritty,Terminal\n")

    assert event is not None
    assert event.name == "openwindow"
    assert event.fields == ("0xabc", "1", "Alacritty", "Terminal")


def test_parse_hyprland_event_ignores_invalid_lines():
    assert parse_hyprland_event("") is None
    assert parse_hyprland_event("not-an-event") is None


def test_ipc_client_uses_short_lived_command_socket():
    sockets: list[FakeCommandSocket] = []

    def socket_factory(_family: int, _type: int):
        sock = FakeCommandSocket([{"address": "0xabc"}])
        sockets.append(sock)
        return sock

    client = HyprlandIpcClient(
        paths=HyprlandSocketPaths(
            command=Path("/tmp/hypr/.socket.sock"),
            events=Path("/tmp/hypr/.socket2.sock"),
        ),
        socket_factory=socket_factory,
    )

    assert client.query_json("clients") == [{"address": "0xabc"}]
    assert sockets[0].connected_to == "/tmp/hypr/.socket.sock"
    assert sockets[0].sent == b"j/clients"
    assert sockets[0].closed is True


def test_hyprland_window_service_publishes_snapshot_and_actions():
    model = _model()
    client = FakeIpcClient(
        [
            (
                [
                    {
                        "address": "abc",
                        "class": "Alacritty",
                        "title": "Terminal",
                        "workspace": {"id": 2, "name": "code"},
                        "at": [10, 20],
                        "size": [800, 600],
                        "fullscreen": 0,
                        "minimized": False,
                    }
                ],
                {"address": "0xabc"},
            )
        ]
    )
    service = HyprlandWindowService(
        model=model,
        **identity_services(),
        client=client,
        event_stream_factory=lambda _callback: None,
    )

    service.start()

    windows = service.list_windows("Alacritty.desktop")
    assert len(windows) == 1
    assert windows[0].id == WindowId(DisplayServer.WAYLAND, "0xabc")
    assert windows[0].title == "Terminal"
    assert windows[0].active is True
    assert windows[0].geometry is not None
    assert windows[0].workspace_id == "2"
    model.update_running.assert_called()

    assert service.activate(windows[0].id) is ActionResult.OK
    assert service.close(windows[0].id) is ActionResult.OK
    assert client.dispatched == [
        "focuswindow address:0xabc",
        "closewindow address:0xabc",
    ]


def test_hyprland_window_service_resolves_preview_handle_from_companion_source():
    handle = object()
    source = SimpleNamespace(
        start=MagicMock(),
        stop=MagicMock(),
        protocol_handle_for_match=MagicMock(return_value=handle),
    )
    client = FakeIpcClient(
        [
            (
                [
                    {
                        "address": "0xabc",
                        "class": "Alacritty",
                        "title": "Terminal",
                    }
                ],
                {"address": "0xabc"},
            )
        ]
    )
    service = HyprlandWindowService(
        model=_model(),
        **identity_services(),
        client=client,
        preview_handle_source=source,
        event_stream_factory=lambda _callback: None,
    )

    service.start()
    window = service.list_windows("Alacritty.desktop")[0]

    assert window.can_preview is True
    assert service.protocol_handle_for_window_id(window.id) is handle
    source.protocol_handle_for_match.assert_called_with(
        desktop_id="Alacritty.desktop",
        app_id="Alacritty",
        title="Terminal",
    )

    service.stop()
    source.start.assert_called_once_with()
    source.stop.assert_called_once_with()


def test_hyprland_window_service_refreshes_on_taskbar_event():
    model = _model()
    streams: list[FakeEventStream] = []
    client = FakeIpcClient(
        [
            ([], {}),
            (
                [
                    {
                        "address": "0xdef",
                        "class": "firefox",
                        "title": "Firefox",
                        "workspace": {"id": 1},
                    }
                ],
                {"address": "0xdef"},
            ),
        ]
    )

    def stream_factory(callback):
        stream = FakeEventStream(callback)
        streams.append(stream)
        return stream

    service = HyprlandWindowService(
        model=model,
        **identity_services(),
        client=client,
        event_stream_factory=stream_factory,
    )

    service.start()
    assert streams[0].started is True
    assert service.list_windows("firefox.desktop") == ()

    streams[0].emit("openwindow", "0xdef,1,firefox,Firefox")

    windows = service.list_windows("firefox.desktop")
    assert len(windows) == 1
    assert windows[0].active is True

    service.stop()
    assert streams[0].stopped is True


def test_hyprland_window_service_ignores_other_backend_window_ids():
    client = FakeIpcClient([([], {})])
    service = HyprlandWindowService(
        model=_model(),
        **identity_services(),
        client=client,
        event_stream_factory=lambda _callback: None,
    )

    service.start()

    assert service.activate(WindowId.x11(1)) is ActionResult.NOT_FOUND
    assert service.close(WindowId.x11(1)) is ActionResult.NOT_FOUND
