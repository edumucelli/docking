"""Optional services must follow advertised APIs, including reduced fallbacks."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import Gio, GLib

from docking.core.config import Config
from docking.platform.backends.gnome import session as gnome
from docking.platform.backends.kwin import session as kwin
from docking.platform.backends.wayland import (
    cosmic_session,
    hyprland_session,
    portals,
    wayfire_session,
)
from docking.platform.backends.wayland.idle import WaylandIdleService
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend
from docking.platform.backends.wayland.toplevels import (
    WaylandStandardToplevelWindowService,
)
from tests.platform.application_fakes import identity_services
from tests.platform.test_gnome_shell_bridge import _bridge
from tests.platform.test_hyprland_ipc import _model
from tests.platform.test_wayland_layer_shell_session import _empty_runtime, _layer_shell


@pytest.mark.parametrize("backend_name", ["hyprland", "cosmic", "wayfire", "kwin"])
@pytest.mark.parametrize("advertised", [False, True])
def test_idle_wiring_is_conditional_and_stops_owned_protocol(
    monkeypatch, backend_name, advertised
):
    runtime = _empty_runtime()
    protocol = (
        SimpleNamespace(start=MagicMock(), stop=MagicMock()) if advertised else None
    )
    runtime.idle_protocol = protocol
    runtime.cosmic_toplevel_protocol = None
    runtime.cosmic_workspace_protocol = None
    runtime.cosmic_overlap_protocol = None
    common = dict(
        layer_shell=_layer_shell(),
        model=_model(),
        protocol_runtime=runtime,
        **identity_services(),
    )
    modules = {
        "hyprland": hyprland_session,
        "cosmic": cosmic_session,
        "wayfire": wayfire_session,
        "kwin": kwin,
    }
    module = modules[backend_name]
    monkeypatch.setattr(module, "load_portal_color_picker", lambda: None)
    if backend_name == "hyprland":
        monkeypatch.setattr(
            module, "load_hyprland_window_service", lambda **_kwargs: None
        )
        backend = module.HyprlandSessionBackend(**common)
    elif backend_name == "cosmic":
        backend = module.CosmicSessionBackend(**common)
    elif backend_name == "wayfire":
        for name in (
            "window_service",
            "workspace_service",
            "window_pick_service",
            "desktop_action_service",
            "preview_service",
        ):
            monkeypatch.setattr(module, "load_wayfire_" + name, lambda **_kwargs: None)
        monkeypatch.setattr(
            module, "load_wayfire_visibility_service", lambda **_kwargs: None
        )
        backend = module.WayfireSessionBackend(config=Config(), **common)
    else:
        monkeypatch.setattr(module.KWinBridgeClient, "connect", lambda: None)
        monkeypatch.setattr(module.KWinDesktopActionService, "connect", lambda: None)
        backend = module.KWinSessionBackend(**common)
    assert backend.capabilities.supports_idle_time is advertised
    assert isinstance(backend.idle, WaylandIdleService) is advertised
    if advertised:
        backend.idle.start()
        protocol.start.assert_called_once_with(backend.idle)
    backend.stop()
    if advertised:
        protocol.stop.assert_called_once()


@pytest.mark.parametrize("available", [False, True])
def test_gnome_idle_portal_and_overlap_follow_real_services(monkeypatch, available):
    idle, picker = (MagicMock(), MagicMock()) if available else (None, None)
    monkeypatch.setattr(gnome.MutterIdleService, "connect", lambda: idle)
    monkeypatch.setattr(gnome, "load_portal_color_picker", lambda: picker)
    backend = gnome.GnomeShellBridgeSessionBackend(
        bridge=_bridge(), model=_model(), **identity_services()
    )
    assert backend.capabilities.supports_idle_time is available
    assert backend.capabilities.supports_screen_color_pick is available
    assert backend.capabilities.supports_overlap_any
    backend.stop()
    if available:
        idle.stop.assert_called_once()
        picker.stop.assert_called_once()


@pytest.mark.parametrize(
    "owned,version,expected",
    [(False, 2, False), (True, 1, False), (True, 2, True), (True, 3, True)],
)
def test_portal_requires_pick_color_interface_version(
    monkeypatch, owned, version, expected
):
    bus = MagicMock()
    bus.call_sync.side_effect = [
        GLib.Variant("(b)", (owned,)),
        GLib.Variant("(v)", (GLib.Variant("u", version),)),
    ]
    monkeypatch.setattr(Gio, "bus_get_sync", lambda *_args: bus)
    assert portals._portal_frontend_available() is expected
    assert bus.call_sync.call_count == (2 if owned else 1)
    for call in bus.call_sync.call_args_list:
        assert call.args[6] == Gio.DBusCallFlags.NO_AUTO_START
        assert call.args[7] == 250


def test_standard_listing_does_not_claim_management_or_focus():
    runtime = _empty_runtime()
    protocol = SimpleNamespace(
        supports_actions=False, start=MagicMock(), stop=MagicMock()
    )
    runtime.standard_toplevel_protocol = protocol
    backend = WaylandLayerShellSessionBackend(
        layer_shell=_layer_shell(),
        model=_model(),
        protocol_runtime=runtime,
        **identity_services(),
    )
    assert isinstance(backend.windows, WaylandStandardToplevelWindowService)
    capabilities = backend.capabilities
    assert capabilities.tracks_windows
    assert not capabilities.supports_activate
    assert not capabilities.tracks_active_window
    assert not capabilities.supports_close
    assert not capabilities.tracks_minimized
    backend.stop()
