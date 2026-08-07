"""Tests for Niri IPC helpers, window service, and preview service."""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
)
from docking.platform.backends.wayland.niri_ipc import (
    NiriDesktopActionService,
    NiriEvent,
    NiriPreviewService,
    NiriWindowService,
    NiriWorkspaceService,
    _niri_socket_path,
    _parse_event_line,
    _parse_response,
    _wait_for_nonempty_file,
    load_niri_desktop_action_service,
    load_niri_preview_service,
    load_niri_workspace_service,
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


# ---------------------------------------------------------------------------
# Socket path detection
# ---------------------------------------------------------------------------


def test_niri_socket_path_from_environment():
    path = _niri_socket_path({"NIRI_SOCKET": "/run/user/1000/niri.sock"})
    assert path is not None
    assert str(path) == "/run/user/1000/niri.sock"


def test_niri_socket_path_missing_environment():
    assert _niri_socket_path({}) is None


def test_niri_socket_path_empty_value():
    assert _niri_socket_path({"NIRI_SOCKET": ""}) is None


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_parse_ok_response():
    result = _parse_response(
        b'{"Ok":{"Windows":[{"id":1,"title":"Terminal","app_id":"Alacritty",'
        b'"is_focused":true,"is_urgent":false}]}}'
    )
    assert "Ok" in result
    assert "Windows" in result["Ok"]
    assert len(result["Ok"]["Windows"]) == 1
    assert result["Ok"]["Windows"][0]["title"] == "Terminal"


def test_parse_error_response():
    result = _parse_response(b'{"Err":"something went wrong"}')
    assert "Err" in result
    assert result["Err"] == "something went wrong"


def test_parse_empty_response():
    assert _parse_response(b"") == {}
    assert _parse_response(b"   ") == {}


def test_parse_response_ignores_non_object_json():
    assert _parse_response(b"[]") == {}
    assert _parse_response(b'"unexpected"') == {}


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------


def test_parse_event_line():
    event = _parse_event_line(
        '{"WindowOpenedOrChanged":{"window":{"id":5,"title":"Firefox",'
        '"app_id":"firefox","is_focused":false,"is_urgent":false}}}'
    )

    assert event is not None
    assert event.name == "WindowOpenedOrChanged"
    assert event.data["window"]["id"] == 5
    assert event.data["window"]["title"] == "Firefox"


def test_parse_event_ignores_ok_ack():
    assert _parse_event_line('{"Ok":"Handled"}') is None


def test_parse_event_ignores_empty():
    assert _parse_event_line("") is None
    assert _parse_event_line("   ") is None


def test_parse_event_ignores_invalid_json():
    assert _parse_event_line("not-json-at-all") is None


# ---------------------------------------------------------------------------
# Fake IPC client for tests
# ---------------------------------------------------------------------------


class FakeIpcClient:
    def __init__(self, snapshots: list[list[dict]]) -> None:
        self._snapshots = snapshots
        self._call_count = 0
        self.actions: list[dict] = []

    def ok_data(self, payload: object, key: str):
        if self._call_count < len(self._snapshots):
            result = self._snapshots[self._call_count]
            self._call_count += 1
            return result
        return None

    def action(self, action_payload: dict) -> ActionResult:
        self.actions.append(action_payload)
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

    def emit(self, name: str, data: dict) -> None:
        self.callback(NiriEvent(name=name, data=data))


class KeyedFakeIpcClient:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data
        self.actions: list[dict] = []

    def ok_data(self, payload: object, key: str):
        return self.data.get(key)

    def action(self, action_payload: dict) -> ActionResult:
        self.actions.append(action_payload)
        return ActionResult.OK


# ---------------------------------------------------------------------------
# Window service tests
# ---------------------------------------------------------------------------


def test_niri_window_service_publishes_snapshot_and_actions():
    model = _model()
    client = FakeIpcClient(
        [
            [
                {
                    "id": 5,
                    "title": "Terminal",
                    "app_id": "Alacritty",
                    "workspace_id": 1,
                    "is_focused": True,
                    "is_urgent": False,
                    "layout": {
                        "window_size": [800, 600],
                        "tile_pos_in_workspace_view": [10.0, 20.0],
                    },
                }
            ],
        ]
    )
    service = NiriWindowService(
        model=model,
        **identity_services(),
        client=client,
        event_stream_factory=lambda _callback: None,
    )

    service.start()

    windows = service.list_windows("Alacritty.desktop")
    assert len(windows) == 1
    assert windows[0].id == WindowId(DisplayServer.WAYLAND, "5")
    assert windows[0].title == "Terminal"
    assert windows[0].active is True
    assert windows[0].geometry is not None
    assert windows[0].geometry.x == 10
    assert windows[0].geometry.y == 20
    assert windows[0].geometry.width == 800
    assert windows[0].geometry.height == 600
    assert windows[0].workspace_id == "1"
    assert windows[0].can_minimize is False  # tiling compositor
    assert windows[0].can_close is True
    assert windows[0].can_activate is True
    model.update_running.assert_called()

    assert service.activate(windows[0].id) is ActionResult.OK
    assert client.actions[-1] == {"FocusWindow": {"id": 5}}

    assert service.close(windows[0].id) is ActionResult.OK
    assert client.actions[-1] == {"CloseWindow": {"id": 5}}


def test_niri_window_service_minimize_unsupported():
    client = FakeIpcClient(
        [
            [
                {
                    "id": 1,
                    "title": "App",
                    "app_id": "Alacritty",
                    "is_focused": True,
                    "is_urgent": False,
                }
            ]
        ]
    )
    service = NiriWindowService(
        model=_model(),
        **identity_services(),
        client=client,
        event_stream_factory=lambda _callback: None,
    )
    service.start()

    _windows = service.list_windows("Alacritty.desktop")
    assert service.minimize_all("Alacritty.desktop") is ActionResult.UNSUPPORTED


def test_niri_window_service_ignores_other_backend_window_ids():
    client = FakeIpcClient([[]])
    service = NiriWindowService(
        model=_model(),
        **identity_services(),
        client=client,
        event_stream_factory=lambda _callback: None,
    )
    service.start()

    assert service.activate(WindowId.x11(1)) is ActionResult.NOT_FOUND
    assert service.close(WindowId.x11(1)) is ActionResult.NOT_FOUND


def test_niri_window_service_refreshes_on_event():
    model = _model()
    streams: list[FakeEventStream] = []
    client = FakeIpcClient([[]])

    def stream_factory(callback):
        stream = FakeEventStream(callback)
        streams.append(stream)
        return stream

    service = NiriWindowService(
        model=model,
        **identity_services(),
        client=client,
        event_stream_factory=stream_factory,
    )

    service.start()
    assert streams[0].started is True
    assert service.list_windows("firefox.desktop") == ()

    # Simulate a window appearing via event stream.
    streams[0].emit(
        "WindowOpenedOrChanged",
        {
            "window": {
                "id": 42,
                "title": "Firefox",
                "app_id": "firefox",
                "workspace_id": 1,
                "is_focused": True,
                "is_urgent": False,
            }
        },
    )

    windows = service.list_windows("firefox.desktop")
    assert len(windows) == 1
    assert windows[0].title == "Firefox"
    assert windows[0].active is True

    # Simulate the window closing.
    streams[0].emit("WindowClosed", {"id": 42})
    assert service.list_windows("firefox.desktop") == ()

    service.stop()
    assert streams[0].stopped is True


def test_niri_window_service_handles_windows_changed_full_replacement():
    model = _model()
    streams: list[FakeEventStream] = []
    client = FakeIpcClient([[]])

    def stream_factory(callback):
        stream = FakeEventStream(callback)
        streams.append(stream)
        return stream

    service = NiriWindowService(
        model=model,
        **identity_services(),
        client=client,
        event_stream_factory=stream_factory,
    )

    service.start()

    # WindowsChanged delivers a full replacement.
    streams[0].emit(
        "WindowsChanged",
        {
            "windows": [
                {
                    "id": 1,
                    "title": "Terminal",
                    "app_id": "Alacritty",
                    "workspace_id": 1,
                    "is_focused": True,
                    "is_urgent": False,
                },
                {
                    "id": 2,
                    "title": "Firefox",
                    "app_id": "firefox",
                    "workspace_id": 1,
                    "is_focused": False,
                    "is_urgent": False,
                },
            ]
        },
    )

    assert len(service.list_windows("Alacritty.desktop")) == 1
    assert len(service.list_windows("firefox.desktop")) == 1

    # A second WindowsChanged replaces everything.
    streams[0].emit(
        "WindowsChanged",
        {
            "windows": [
                {
                    "id": 3,
                    "title": "New Window",
                    "app_id": "firefox",
                    "workspace_id": 2,
                    "is_focused": True,
                    "is_urgent": False,
                },
            ]
        },
    )

    assert service.list_windows("Alacritty.desktop") == ()
    assert len(service.list_windows("firefox.desktop")) == 1
    assert service.list_windows("firefox.desktop")[0].title == "New Window"

    service.stop()


def test_niri_window_service_handles_focus_change():
    model = _model()
    streams: list[FakeEventStream] = []
    client = FakeIpcClient(
        [
            [
                {
                    "id": 1,
                    "title": "Terminal",
                    "app_id": "Alacritty",
                    "is_focused": False,
                    "is_urgent": False,
                },
                {
                    "id": 2,
                    "title": "Firefox",
                    "app_id": "firefox",
                    "is_focused": True,
                    "is_urgent": False,
                },
            ]
        ]
    )

    def stream_factory(callback):
        stream = FakeEventStream(callback)
        streams.append(stream)
        return stream

    service = NiriWindowService(
        model=model,
        **identity_services(),
        client=client,
        event_stream_factory=stream_factory,
    )

    service.start()

    # Initially Firefox is focused.
    windows = service.list_windows("Alacritty.desktop")
    assert windows[0].active is False
    firefox = service.list_windows("firefox.desktop")
    assert firefox[0].active is True

    # Focus changes to Alacritty.
    streams[0].emit("WindowFocusChanged", {"id": 1})

    windows = service.list_windows("Alacritty.desktop")
    assert windows[0].active is True
    firefox = service.list_windows("firefox.desktop")
    assert firefox[0].active is False

    # Focus goes to nothing (id: null).
    streams[0].emit("WindowFocusChanged", {"id": None})

    windows = service.list_windows("Alacritty.desktop")
    assert windows[0].active is False

    service.stop()


def test_niri_window_service_close_all():
    client = FakeIpcClient(
        [
            [
                {
                    "id": 1,
                    "title": "Firefox Tab 1",
                    "app_id": "firefox",
                    "is_focused": True,
                    "is_urgent": False,
                },
                {
                    "id": 2,
                    "title": "Firefox Tab 2",
                    "app_id": "firefox",
                    "is_focused": False,
                    "is_urgent": False,
                },
            ]
        ]
    )
    service = NiriWindowService(
        model=_model(),
        **identity_services(),
        client=client,
        event_stream_factory=lambda _callback: None,
    )
    service.start()

    result = service.close_all("firefox.desktop")
    assert result is ActionResult.OK
    assert len(client.actions) == 2
    assert client.actions[0] == {"CloseWindow": {"id": 1}}
    assert client.actions[1] == {"CloseWindow": {"id": 2}}


def test_niri_window_service_close_all_not_found():
    client = FakeIpcClient([[]])
    service = NiriWindowService(
        model=_model(),
        **identity_services(),
        client=client,
        event_stream_factory=lambda _callback: None,
    )
    service.start()

    assert service.close_all("nonexistent.desktop") is ActionResult.NOT_FOUND


# ---------------------------------------------------------------------------
# Workspace service tests
# ---------------------------------------------------------------------------


def test_niri_workspace_service_lists_and_activates_workspaces():
    client = KeyedFakeIpcClient(
        {
            "Workspaces": [
                {
                    "id": 1,
                    "idx": 1,
                    "name": None,
                    "output": "HDMI-A-1",
                    "is_active": True,
                    "is_focused": True,
                },
                {
                    "id": 3,
                    "idx": 2,
                    "name": None,
                    "output": "HDMI-A-1",
                    "is_active": False,
                    "is_focused": False,
                },
                {
                    "id": 2,
                    "idx": 1,
                    "name": None,
                    "output": "eDP-1",
                    "is_active": True,
                    "is_focused": False,
                },
            ]
        }
    )
    service = NiriWorkspaceService(
        client=client, event_stream_factory=lambda _callback: None
    )

    service.start()

    workspaces = service.list_workspaces()
    assert [workspace.id for workspace in workspaces] == ["1", "3", "2"]
    assert [workspace.number for workspace in workspaces] == [0, 1, 2]
    assert service.active_workspace() == workspaces[0]

    assert service.activate("3") is ActionResult.OK
    assert client.actions == [
        {"FocusMonitor": {"output": "HDMI-A-1"}},
        {"FocusWorkspace": {"reference": {"Index": 2}}},
    ]


def test_niri_workspace_service_notifies_on_workspace_events():
    stream: FakeEventStream | None = None

    def stream_factory(callback):
        nonlocal stream
        stream = FakeEventStream(callback)
        return stream

    client = KeyedFakeIpcClient(
        {
            "Workspaces": [
                {
                    "id": 1,
                    "idx": 1,
                    "output": "HDMI-A-1",
                    "is_focused": True,
                }
            ]
        }
    )
    service = NiriWorkspaceService(
        client=client,
        event_stream_factory=stream_factory,
    )
    changed: list[str] = []
    watch = service.watch_active_workspace(lambda: changed.append("changed"))

    service.start()
    assert stream is not None
    assert changed == ["changed"]

    stream.emit(
        "WorkspacesChanged",
        {
            "workspaces": [
                {
                    "id": 1,
                    "idx": 1,
                    "output": "HDMI-A-1",
                    "is_focused": False,
                },
                {
                    "id": 3,
                    "idx": 2,
                    "output": "HDMI-A-1",
                    "is_focused": True,
                },
            ]
        },
    )
    assert changed == ["changed", "changed"]

    service.unwatch_active_workspace(watch)
    stream.emit(
        "WorkspacesChanged",
        {
            "workspaces": [
                {
                    "id": 1,
                    "idx": 1,
                    "output": "HDMI-A-1",
                    "is_focused": True,
                }
            ]
        },
    )
    assert changed == ["changed", "changed"]


# ---------------------------------------------------------------------------
# Desktop action service tests
# ---------------------------------------------------------------------------


def test_niri_desktop_action_service_focuses_empty_workspace_on_current_output():
    client = KeyedFakeIpcClient(
        {
            "Workspaces": [
                {
                    "id": 1,
                    "idx": 1,
                    "output": "HDMI-A-1",
                    "is_focused": True,
                    "active_window_id": 6,
                },
                {
                    "id": 3,
                    "idx": 2,
                    "output": "HDMI-A-1",
                    "is_focused": False,
                    "active_window_id": None,
                },
                {
                    "id": 2,
                    "idx": 1,
                    "output": "eDP-1",
                    "is_focused": False,
                    "active_window_id": None,
                },
            ]
        }
    )
    service = NiriDesktopActionService(client=client)

    assert service.show_desktop() is ActionResult.OK

    assert client.actions == [
        {"FocusMonitor": {"output": "HDMI-A-1"}},
        {"FocusWorkspace": {"reference": {"Index": 2}}},
    ]


def test_niri_desktop_action_service_restores_previous_workspace_on_toggle():
    client = KeyedFakeIpcClient(
        {
            "Workspaces": [
                {
                    "id": 1,
                    "idx": 1,
                    "output": "HDMI-A-1",
                    "is_focused": True,
                    "active_window_id": 6,
                },
                {
                    "id": 3,
                    "idx": 2,
                    "output": "HDMI-A-1",
                    "is_focused": False,
                    "active_window_id": None,
                },
            ]
        }
    )
    service = NiriDesktopActionService(client=client)

    assert service.show_desktop() is ActionResult.OK
    client.actions.clear()
    client.data["Workspaces"] = [
        {
            "id": 1,
            "idx": 1,
            "output": "HDMI-A-1",
            "is_focused": False,
            "active_window_id": 6,
        },
        {
            "id": 3,
            "idx": 2,
            "output": "HDMI-A-1",
            "is_focused": True,
            "active_window_id": None,
        },
    ]

    assert service.show_desktop() is ActionResult.OK

    assert client.actions == [
        {"FocusMonitor": {"output": "HDMI-A-1"}},
        {"FocusWorkspace": {"reference": {"Index": 1}}},
    ]


def test_niri_desktop_action_service_ignores_empty_workspaces_on_other_outputs():
    client = KeyedFakeIpcClient(
        {
            "Workspaces": [
                {
                    "id": 1,
                    "idx": 1,
                    "output": "HDMI-A-1",
                    "is_focused": True,
                    "active_window_id": 6,
                },
                {
                    "id": 2,
                    "idx": 1,
                    "output": "eDP-1",
                    "is_focused": False,
                    "active_window_id": None,
                },
            ]
        }
    )
    service = NiriDesktopActionService(client=client)

    assert service.show_desktop() is ActionResult.NOT_FOUND
    assert client.actions == []


# ---------------------------------------------------------------------------
# Preview service tests
# ---------------------------------------------------------------------------


def _fake_socket_factory(response: bytes) -> type[socket.socket]:
    """Return a socket factory that produces a socket returning *response*."""

    class _FakeSock:
        def __init__(self, family: int, type: int) -> None:
            self._response = response
            self._offset = 0
            self.settimeout = MagicMock()
            self.shutdown = MagicMock()

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            pass

        def connect(self, path: str) -> None:
            pass

        def sendall(self, data: bytes) -> None:
            pass

        def recv(self, bufsize: int) -> bytes:
            if self._offset >= len(self._response):
                return b""
            chunk = self._response[self._offset : self._offset + bufsize]
            self._offset += bufsize
            return chunk

    return _FakeSock  # type: ignore[return-value]


def test_niri_preview_service_captures_window():
    """Preview should return a scaled image when the compositor saves a PNG."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    # Create an in-memory test PNG.
    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 48, 36)
    pixbuf.fill(0xFF_80_40_FF)  # RGBA

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png", prefix="docking-test-")
    pixbuf.savev(tmp_path, "png", [], [])
    # Keep tmp_fd open so _capture_impl can close it without EBADF.

    try:
        svc = NiriPreviewService(socket_path="/nonexistent/sock")

        with (
            patch("socket.socket", _fake_socket_factory(b'{"Ok":"Handled"}')),
            patch(
                "docking.platform.backends.wayland.niri_ipc._wait_for_nonempty_file",
                return_value=True,
            ),
            patch(
                "docking.platform.backends.wayland.niri_ipc.tempfile.mkstemp",
                return_value=(tmp_fd, tmp_path),
            ),
        ):
            img = svc.capture(
                WindowId(backend=DisplayServer.WAYLAND, value="3"),
                width=24,
                height=18,
            )
    finally:
        with __import__("contextlib").suppress(OSError):
            os.close(tmp_fd)
        with __import__("contextlib").suppress(OSError):
            Path(tmp_path).unlink()

    assert img is not None
    assert img.width == 24
    assert img.height == 18


def test_niri_preview_service_rejects_non_wayland_ids():
    svc = NiriPreviewService(socket_path="/nonexistent/sock")
    assert svc.capture(WindowId.x11(1), width=100, height=100) is None


def test_niri_preview_service_returns_none_on_socket_error():
    svc = NiriPreviewService(socket_path="/nonexistent/sock")
    with patch("socket.socket", side_effect=OSError("connect failed")):
        img = svc.capture(
            WindowId(backend=DisplayServer.WAYLAND, value="1"),
            width=100,
            height=100,
        )
    assert img is None


def test_niri_preview_service_start_stop_are_noops():
    svc = NiriPreviewService(socket_path="/nonexistent/sock")
    svc.start()
    svc.stop()  # no exceptions


def test_wait_for_nonempty_file_sees_existing_file():
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="docking-test-")
    os.write(tmp_fd, b"hello")
    os.close(tmp_fd)
    try:
        assert _wait_for_nonempty_file(tmp_path, timeout=0.5) is True
    finally:
        Path(tmp_path).unlink()


def test_wait_for_nonempty_file_times_out_for_missing_file():
    assert _wait_for_nonempty_file("/nonexistent/path/12345", timeout=0.1) is False


def test_load_niri_preview_service_returns_none_without_env():
    with patch.dict("os.environ", {}, clear=True):
        assert load_niri_preview_service() is None


def test_load_niri_workspace_service_returns_none_without_env():
    with patch.dict("os.environ", {}, clear=True):
        assert load_niri_workspace_service() is None


def test_load_niri_desktop_action_service_returns_none_without_env():
    with patch.dict("os.environ", {}, clear=True):
        assert load_niri_desktop_action_service() is None
