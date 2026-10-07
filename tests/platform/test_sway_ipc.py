"""Sway visibility/actions and real framed-socket transport regressions."""

import json
import socket
import threading

import pytest

from docking.platform.backends.base import ActionResult, DisplayServer, Rect, WindowId
from docking.platform.backends.wayland.sway_ipc import (
    _HEADER,
    SwayIpcClient,
    SwayWindowService,
    SwayWorkspaceService,
    windows_from_tree,
)
from tests.platform.application_fakes import identity_services
from tests.platform.test_hyprland_ipc import _model


def tree():
    return {
        "type": "root",
        "nodes": [
            {
                "type": "workspace",
                "id": 1,
                "layout": "tabbed",
                "focus": [12],
                "nodes": [
                    {
                        "id": 11,
                        "app_id": "Alacritty",
                        "focused": False,
                        "rect": {"x": -1280, "y": 100, "width": 1280, "height": 720},
                    },
                    {
                        "id": 12,
                        "app_id": "firefox",
                        "focused": True,
                        "rect": {"x": -1280, "y": 100, "width": 1280, "height": 720},
                    },
                ],
                "floating_nodes": [
                    {
                        "id": 13,
                        "app_id": "Alacritty",
                        "rect": {"x": -500, "y": 200, "width": 300, "height": 200},
                    }
                ],
            },
            {
                "type": "workspace",
                "id": 2,
                "nodes": [
                    {
                        "id": 14,
                        "app_id": "firefox",
                        "rect": {"x": 0, "y": 0, "width": 300, "height": 200},
                    }
                ],
            },
        ],
    }


def test_global_rectangles_and_tabbed_floating_hidden_workspaces():
    rows = windows_from_tree(tree(), {"1"})
    assert [row.visible for row in rows] == [False, True, True, False]
    assert rows[1].geometry == Rect(-1280, 100, 1280, 720)
    assert rows[1].active
    assert not rows[1].can_minimize


class Client:
    def __init__(self):
        self.commands = []

    def query(self, kind):
        return (
            tree()
            if kind == 4
            else [{"id": 1, "name": '1: "Work"', "visible": True, "focused": True}]
        )

    def command(self, command):
        self.commands.append(command)
        return ActionResult.OK


def test_native_actions_and_workspace_names_are_not_command_injection():
    client = Client()
    service = SwayWindowService(model=_model(), client=client, **identity_services())
    service.refresh()
    row = service.list_windows("firefox.desktop")[0]
    assert service.activate(row.id) is ActionResult.OK
    assert client.commands[-1] == "[con_id=12] focus"
    assert service.minimize_all("firefox.desktop") is ActionResult.UNSUPPORTED
    assert (
        service.activate(WindowId(DisplayServer.X11, row.id.value))
        is ActionResult.NOT_FOUND
    )
    assert service.close(row.id) is ActionResult.OK
    assert client.commands[-1] == "[con_id=12] kill"
    workspaces = SwayWorkspaceService(client=client, windows=service)
    assert workspaces.activate("1") is ActionResult.OK
    assert client.commands[-1] == 'workspace "1: \\"Work\\""'
    assert workspaces.activate("999") is ActionResult.NOT_FOUND
    service.stop()


@pytest.mark.parametrize("invalid", [False, True])
def test_ipc_reads_fragmented_frames_and_rejects_invalid_headers(tmp_path, invalid):
    path = tmp_path / "sway.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen()

    def respond():
        with server, server.accept()[0] as peer:
            peer.recv(100)
            payload = json.dumps({"native": True}).encode()
            frame = (
                _HEADER.pack(b"bad-ip" if invalid else b"i3-ipc", len(payload), 4)
                + payload
            )
            for byte in frame:
                try:
                    peer.sendall(bytes([byte]))
                except BrokenPipeError:
                    assert invalid
                    break

    thread = threading.Thread(target=respond)
    thread.start()
    try:
        if invalid:
            with pytest.raises(OSError, match="Invalid Sway"):
                SwayIpcClient(str(path)).query(4)
        else:
            assert SwayIpcClient(str(path)).query(4) == {"native": True}
    finally:
        thread.join(timeout=1)
