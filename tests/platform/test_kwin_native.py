"""Native UUID authority, raw-pixel decoding, fallback and script ownership."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.core.config import Config
from docking.platform.backends.base import ActionResult, DisplayServer, Rect, WindowId
from docking.platform.backends.kwin import session
from docking.platform.backends.kwin.bridge import (
    KWinBridgeClient,
    KWinWindowService,
    snapshots_from_json,
)
from docking.platform.backends.kwin.preview import (
    KWinPreviewService,
    decode_raw_image,
    raw_image_size,
)
from docking.platform.backends.visibility import SnapshotVisibilityService
from tests.platform.application_fakes import identity_services
from tests.platform.test_hyprland_ipc import _model
from tests.platform.test_wayland_layer_shell_session import _empty_runtime, _layer_shell

IDENTIFIER = "{11111111-2222-3333-4444-555555555555}"


def row(**changes):
    return {
        "id": IDENTIFIER,
        "title": "Native fixture",
        "app_id": "Alacritty",
        "pid": 12345,
        "active": True,
        "geometry": [-1280.2, 10.2, 640.4, 480.4],
        "visible": True,
        "can_activate": True,
        "can_minimize": True,
        "can_close": True,
        **changes,
    }


def test_native_uuid_and_outward_rounded_global_geometry():
    window = snapshots_from_json(json.dumps([row()]))[0]
    assert window.id == WindowId(DisplayServer.WAYLAND, f"kwin:{IDENTIFIER}")
    assert window.pid == 12345 and window.visible
    assert window.geometry == Rect(-1281, 10, 642, 481)


@pytest.mark.parametrize("identifier", ["", "atspi:/window/1", "7", "'; close_all();'"])
def test_non_authoritative_ids_are_not_used_for_actions_or_previews(identifier):
    assert not snapshots_from_json(json.dumps([row(id=identifier)]))
    service = KWinPreviewService()
    service._bus = MagicMock()
    assert (
        service.capture(
            WindowId(DisplayServer.WAYLAND, identifier), width=100, height=100
        )
        is None
    )
    service._bus.call_with_unix_fd_list.assert_not_called()


def test_native_window_service_projects_identity_and_checks_stale_ids():
    bridge = MagicMock(spec=KWinBridgeClient)
    bridge.list_windows.return_value = snapshots_from_json(json.dumps([row()]))
    bridge.action.return_value = ActionResult.OK
    service = KWinWindowService(bridge=bridge, model=_model(), **identity_services())
    service.start()
    window = service.list_windows("Alacritty.desktop")[0]
    assert service.activate(window.id) is ActionResult.OK
    bridge.action.assert_called_with(window.id, "activate")
    bridge.list_windows.return_value = ()
    service.refresh()
    bridge.action.reset_mock()
    assert service.close(window.id) is ActionResult.NOT_FOUND
    bridge.action.assert_not_called()
    service.stop()
    bridge.unwatch.assert_called_once()
    bridge.stop.assert_called_once()


def test_skip_taskbar_dialog_retains_geometry_without_a_running_icon():
    bridge = MagicMock(spec=KWinBridgeClient)
    bridge.list_windows.return_value = snapshots_from_json(
        json.dumps([row(taskbar=False, dialog=True)])
    )
    model = _model()
    service = KWinWindowService(bridge=bridge, model=model, **identity_services())
    service.start()
    try:
        assert service.list_all_windows()[0].skip_taskbar
        assert service.list_all_windows()[0].geometry is not None
        assert not service.list_windows("Alacritty.desktop")
        model.update_running.assert_called_with(running={})
    finally:
        service.stop()


def test_current_workspace_filter_keeps_minimized_windows_and_reacts_to_settings():
    bridge = MagicMock(spec=KWinBridgeClient)
    bridge.list_windows.return_value = snapshots_from_json(
        json.dumps(
            [
                row(on_current_workspace=True, minimized=True, visible=False),
                row(
                    id="{99999999-2222-3333-4444-555555555555}",
                    on_current_workspace=False,
                    visible=False,
                ),
            ]
        )
    )
    config = Config(current_workspace_only=True)
    service = KWinWindowService(
        bridge=bridge, model=_model(), config=config, **identity_services()
    )
    service.start()
    try:
        assert len(service.list_all_windows()) == 2
        assert len(service.list_windows("Alacritty.desktop")) == 1
        assert service.list_windows("Alacritty.desktop")[0].minimized
        config.current_workspace_only = False
        service.refresh()
        assert len(service.list_windows("Alacritty.desktop")) == 2
    finally:
        service.stop()


def metadata(**changes):
    return {"type": "raw", "format": 6, "width": 1, "height": 1, "stride": 4, **changes}


@pytest.mark.parametrize(
    "changes",
    [
        {"type": "png"},
        {"width": 0},
        {"width": True},
        {"height": -1},
        {"stride": 3},
        {"height": 16385},
        {"width": 16384, "height": 16384, "stride": 65536},
        {"format": 999},
        {"stride": None},
    ],
)
def test_invalid_raw_metadata_is_rejected(changes):
    assert raw_image_size(metadata(**changes)) is None


def test_capture_setup_failure_does_not_leak_fds(monkeypatch):
    from gi.repository import GLib

    service = KWinPreviewService()
    service._bus = MagicMock()
    service._bus.call_with_unix_fd_list.side_effect = GLib.Error("closed bus")
    before = len(list(Path("/proc/self/fd").iterdir()))
    assert (
        service.capture(
            WindowId(DisplayServer.WAYLAND, f"kwin:{IDENTIFIER}"), width=100, height=100
        )
        is None
    )
    assert len(list(Path("/proc/self/fd").iterdir())) == before
    assert service._capture is None


def test_argb_premultiplied_is_unpremultiplied_and_transparency_preserved():
    word = (128 << 24) | (128 << 16) | (32 << 8) | 16
    pixbuf = decode_raw_image(word.to_bytes(4, sys.byteorder), metadata())
    assert pixbuf is not None
    pixels = pixbuf.get_pixels()
    assert pixels[0] == 255 and 63 <= pixels[1] <= 64 and 31 <= pixels[2] <= 32
    assert pixels[3] == 128


def test_rgba_and_padded_row_stride_are_not_treated_as_encoded_images():
    pixbuf = decode_raw_image(
        bytes([255, 1, 2, 127, 0, 0, 0, 0]), metadata(format=17, stride=8)
    )
    assert pixbuf is not None and list(pixbuf.get_pixels())[:4] == [255, 1, 2, 127]
    assert decode_raw_image(b"truncated", metadata()) is None


@pytest.mark.parametrize("native", [False, True])
def test_kwin_backend_native_or_atspi_fallback_capabilities(monkeypatch, native):
    bridge = MagicMock(spec=KWinBridgeClient) if native else None
    monkeypatch.setattr(session.KWinBridgeClient, "connect", lambda: bridge)
    monkeypatch.setattr(session.KWinDesktopActionService, "connect", lambda: None)
    monkeypatch.setattr(session, "load_portal_color_picker", lambda: None)
    backend = session.KWinSessionBackend(
        layer_shell=_layer_shell(),
        model=_model(),
        protocol_runtime=_empty_runtime(),
        **identity_services(),
    )
    assert backend.capabilities.supports_activate is native
    assert backend.capabilities.supports_overlap_any is native
    assert isinstance(backend.visibility, SnapshotVisibilityService) is native
    assert not backend.capabilities.supports_show_desktop
    backend.stop()


def test_owned_script_runs_only_its_object_not_scripting_start():
    client = KWinBridgeClient(bus=MagicMock(), proxy=MagicMock())
    client._call = MagicMock(side_effect=[SimpleNamespace(unpack=lambda: (42,)), None])
    try:
        client._load("print('owned');", client._plugin)
        calls = client._call.call_args_list
        assert calls[0].args[2] == "loadScript"
        assert calls[1].args[:3] == (
            "/Scripting/Script42",
            "org.kde.kwin.Script",
            "run",
        )
        assert all(call.args[2] != "start" for call in calls)
    finally:
        client._call = MagicMock()
        client.stop()


def test_bridge_rejects_publications_not_from_current_compositor():
    proxy = MagicMock()
    proxy.get_name_owner.return_value = ":1.2"
    proxy.get_cached_property.return_value = None
    client = KWinBridgeClient(bus=MagicMock(), proxy=proxy)
    invocation = MagicMock()
    try:
        client._method(
            None,
            ":1.3",
            client._path,
            "org.docking.KWinBridge",
            "Publish",
            SimpleNamespace(unpack=lambda: (json.dumps([row()]),)),
            invocation,
        )
        invocation.return_dbus_error.assert_called_once()
        assert client.list_windows() == ()
    finally:
        client.stop()
