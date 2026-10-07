"""Niri layout values are workspace-local and possibly fractional/absent."""

from unittest.mock import MagicMock

import pytest

from docking.platform.backends.base import Rect
from docking.platform.backends.wayland.niri_ipc import (
    NiriEvent,
    NiriWindowService,
    _geometry_from_niri_window,
)
from tests.platform.application_fakes import identity_services
from tests.platform.test_niri_ipc import KeyedFakeIpcClient, _model


def test_event_delivery_coalesces_and_bounds_a_backlog(monkeypatch):
    service = NiriWindowService(
        model=_model(),
        **identity_services(),
        client=KeyedFakeIpcClient({}),
        event_stream_factory=lambda _callback: None,
    )
    service.start()
    monkeypatch.setattr(
        "docking.platform.backends.wayland.niri_ipc.GLib.idle_add",
        MagicMock(return_value=99),
    )
    monkeypatch.setattr(
        "docking.platform.backends.wayland.niri_ipc.GLib.source_remove", MagicMock()
    )
    service.refresh = MagicMock()
    service._on_event = MagicMock()
    for index in range(300):
        service._queue_event(
            NiriEvent("WindowLayoutsChanged", {"changes": [[index, {}]]})
        )
    assert service._pending == 99
    assert len(service._events) <= 256
    service._dispatch_events()
    service.refresh.assert_called_once()
    assert service._pending == 0
    service.stop()


def test_tile_geometry_includes_output_origin_and_rounds_outward():
    layout = {
        "tile_pos_in_workspace_view": [100.4, 50.2],
        "tile_size": [640.4, 480.4],
        "window_size": [600, 400],
        "window_offset_in_tile": [10, 20],
    }
    assert _geometry_from_niri_window(
        {"layout": layout}, output={"x": -1280, "y": 200}
    ) == Rect(-1180, 250, 641, 481)


def test_window_only_geometry_includes_window_offset():
    layout = {
        "tile_pos_in_workspace_view": [100, 50],
        "window_size": [640, 480],
        "window_offset_in_tile": [10, 20],
    }
    assert _geometry_from_niri_window(
        {"layout": layout}, output={"x": 1280, "y": -100}
    ) == Rect(1390, -30, 640, 480)


@pytest.mark.parametrize(
    "layout",
    [
        {"window_size": [640, 480]},
        {"tile_size": [640, 480], "tile_pos_in_workspace_view": None},
        {"tile_size": [640, 480], "tile_pos_in_workspace_view": [float("nan"), 10]},
        {"tile_size": [-1, 480], "tile_pos_in_workspace_view": [0, 0]},
        {"tile_size": [640, float("inf")], "tile_pos_in_workspace_view": [0, 0]},
    ],
)
def test_missing_invalid_geometry_is_not_fabricated(layout):
    assert (
        _geometry_from_niri_window({"layout": layout}, output={"x": 0, "y": 0}) is None
    )


def test_unknown_output_does_not_become_origin():
    assert (
        _geometry_from_niri_window(
            {
                "layout": {
                    "tile_size": [640, 480],
                    "tile_pos_in_workspace_view": [100, 50],
                }
            }
        )
        is None
    )


def test_layout_event_updates_geometry_and_workspace_visibility():
    client = KeyedFakeIpcClient(
        {
            "Outputs": {"HEADLESS-2": {"logical": {"x": 1280, "y": 100}}},
            "Workspaces": [{"id": 1, "output": "HEADLESS-2", "is_active": True}],
            "Windows": [
                {
                    "id": 5,
                    "app_id": "Alacritty",
                    "workspace_id": 1,
                    "is_focused": True,
                    "layout": {
                        "tile_pos_in_workspace_view": [0, 0],
                        "tile_size": [600, 400],
                    },
                }
            ],
        }
    )
    service = NiriWindowService(
        model=_model(),
        client=client,
        **identity_services(),
        event_stream_factory=lambda _callback: None,
    )
    service.start()
    notifications = []
    service.watch(lambda: notifications.append(True))
    assert service.list_all_windows()[0].geometry == Rect(1280, 100, 600, 400)
    service._on_event(
        NiriEvent(
            "WindowLayoutsChanged",
            {
                "changes": [
                    [
                        5,
                        {
                            "tile_pos_in_workspace_view": [10, 20],
                            "tile_size": [600, 700],
                        },
                    ]
                ]
            },
        )
    )
    assert service.list_all_windows()[0].geometry == Rect(1290, 120, 600, 700)
    client.data["Workspaces"][0]["is_active"] = False
    service._on_event(NiriEvent("WorkspaceActivated", {"id": 2, "focused": False}))
    assert service.list_all_windows()[0].visible is False
    assert len(notifications) == 2
    service.stop()
