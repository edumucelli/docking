"""Cinnamon Wayland session using layer-shell and Muffin window snapshots."""

from __future__ import annotations

from dataclasses import replace

from docking.platform.backends.base import PlatformCapabilities
from docking.platform.backends.cinnamon.muffin import (
    MuffinDebugClient,
    MuffinWindowService,
)
from docking.platform.backends.wayland.session import WaylandLayerShellSessionBackend


class CinnamonWaylandSessionBackend(WaylandLayerShellSessionBackend):
    def __init__(
        self, *, layer_shell: object, model, launcher, client: MuffinDebugClient
    ):
        super().__init__(layer_shell=layer_shell, model=model, launcher=launcher)
        self._services = replace(
            self._services,
            windows=MuffinWindowService(
                model=model,
                launcher=launcher,
                client=client,
            ),
        )

    @property
    def name(self) -> str:
        return "cinnamon-wayland"

    @property
    def capabilities(self) -> PlatformCapabilities:
        base = super().capabilities
        return replace(
            base,
            tracks_windows=True,
            tracks_active_window=True,
            tracks_attention=True,
            tracks_minimized=False,
            tracks_maximized=False,
            tracks_fullscreen=False,
            tracks_window_geometry=True,
            tracks_window_workspace=True,
            supports_activate=False,
            supports_minimize=False,
            supports_close=False,
            supports_window_menu=True,
        )
