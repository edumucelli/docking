# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""KWin / KDE Plasma 6 native Wayland session backend.

Backend selection::

    KDE Plasma Wayland session  ---->  KWinSessionBackend
                                       |
                                       +-- WaylandLayerShellSurfaceService
                                       +-- KWinWorkspaceService
                                       |     (via KWin VirtualDesktopManager D-Bus)
                                       +-- ReducedWindowService
                                       |     (window listing not available from
                                       |      KWin 6 Wayland - KWin does not
                                       |      expose a public window-list protocol)
                                       +-- ReducedVisibilityService

KWin 6 (Plasma 6) does not expose ``wlr-foreign-toplevel-management``,
``ext-foreign-toplevel-list``, or ``org_kde_plasma_window_management``
to third-party Wayland clients.  Its scripting API does not provide a
usable D-Bus bridge.  Window tracking is therefore unavailable in this
backend until KWin adds a public window-list protocol or D-Bus API.

The backend still delivers native Wayland layer-shell positioning and
proper workspace tracking via KWin's ``VirtualDesktopManager`` D-Bus
interface.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

from gi.repository import Gio, GLib

from docking.platform.backends.base import (
    ActionResult,
    DesktopActionService,
    DisplayServer,
    IdleService,
    PlatformCapabilities,
    PreviewService,
    ScreenCaptureService,
    SessionBackend,
    SurfaceService,
    VisibilityService,
    WindowPickService,
    WindowService,
    WorkspaceService,
    WorkspaceSnapshot,
)
from docking.platform.backends.kwin.atspi_window import (
    AtspiWindowService,
)
from docking.platform.backends.reduced.services import (
    ReducedPreviewService,
    ReducedVisibilityService,
)

if TYPE_CHECKING:
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel

import contextlib

from docking.log import get_logger

log = get_logger(name="kwin_backend")


# ---------------------------------------------------------------------------
# Workspace Service (KWin VirtualDesktopManager D-Bus)
# ---------------------------------------------------------------------------


class KWinWorkspaceService(WorkspaceService):
    """WorkspaceService backed by KWin's D-Bus VirtualDesktopManager."""

    def __init__(self) -> None:
        self._active_id: str = ""
        self._workspaces: dict[str, WorkspaceSnapshot] = {}
        self._watchers: dict[object, Callable[[], None]] = {}
        self._signal_id: int = 0
        self._proxy: Gio.DBusProxy | None = None

    def start(self) -> None:
        try:
            self._proxy = Gio.DBusProxy.new_for_bus_sync(
                Gio.BusType.SESSION,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.kde.KWin",
                "/VirtualDesktopManager",
                "org.kde.KWin.VirtualDesktopManager",
                None,
            )
            self._refresh()
            self._signal_id = self._proxy.connect(
                "g-properties-changed",
                self._on_properties_changed,
            )
            log.info("KWin workspace service: connected to VirtualDesktopManager")
        except Exception:
            log.exception("KWin workspace service: D-Bus connection failed")
            self._proxy = None

    def stop(self) -> None:
        if self._proxy is not None and self._signal_id:
            self._proxy.disconnect(self._signal_id)
            self._signal_id = 0
        self._proxy = None
        self._workspaces.clear()
        self._watchers.clear()

    def list_workspaces(self) -> Sequence[WorkspaceSnapshot]:
        return list(self._workspaces.values())

    def active_workspace(self) -> WorkspaceSnapshot | None:
        return self._workspaces.get(self._active_id)

    def activate(self, workspace_id: str) -> ActionResult:
        if self._proxy is None:
            return ActionResult.UNSUPPORTED
        try:
            variant = GLib.Variant(
                "(ssv)",
                (
                    "org.kde.KWin.VirtualDesktopManager",
                    "current",
                    GLib.Variant("s", workspace_id),
                ),
            )
            self._proxy.call_sync(
                "org.freedesktop.DBus.Properties.Set",
                variant,
                Gio.DBusCallFlags.NONE,
                500,
                None,
            )
            return ActionResult.OK
        except Exception:
            return ActionResult.FAILED

    def watch_active_workspace(self, on_change: Callable[[], None]) -> object | None:
        handle = object()
        self._watchers[handle] = on_change
        return handle

    def unwatch_active_workspace(self, handle: object) -> None:
        self._watchers.pop(handle, None)

    def _refresh(self) -> None:
        if self._proxy is None:
            return
        try:
            current = self._proxy.get_cached_property("current")
            if current is not None:
                self._active_id = current.get_string()
        except Exception:
            pass

        try:
            desktops_v = self._proxy.get_cached_property("desktops")
            count_v = self._proxy.get_cached_property("count")
            if desktops_v is not None and count_v is not None:
                desktops = desktops_v.unpack()
                n = count_v.get_uint32()
                self._workspaces.clear()
                for entry in desktops[:n]:
                    # Each entry is (position, id, name)
                    pos, ws_id, name = entry
                    self._workspaces[str(ws_id)] = WorkspaceSnapshot(
                        id=str(ws_id),
                        number=int(pos) + 1,
                        name=str(name),
                        active=(str(ws_id) == self._active_id),
                    )
        except Exception:
            log.exception("KWin workspace service: failed to parse workspaces")

    def _on_properties_changed(
        self,
        proxy: Gio.DBusProxy,
        changed: GLib.Variant,
        _invalidated: list[str],
    ) -> None:
        changed_dict = changed.unpack()
        needs_refresh = False

        if "current" in changed_dict:
            new_active = str(changed_dict["current"])
            if new_active != self._active_id:
                prev = self._workspaces.get(self._active_id)
                if prev is not None:
                    self._workspaces[self._active_id] = WorkspaceSnapshot(
                        id=prev.id,
                        number=prev.number,
                        name=prev.name,
                        active=False,
                    )
                self._active_id = new_active
                curr = self._workspaces.get(new_active)
                if curr is not None:
                    self._workspaces[new_active] = WorkspaceSnapshot(
                        id=curr.id,
                        number=curr.number,
                        name=curr.name,
                        active=True,
                    )
                for cb in list(self._watchers.values()):
                    with contextlib.suppress(Exception):
                        cb()

        if "desktops" in changed_dict or "count" in changed_dict:
            needs_refresh = True

        if needs_refresh:
            self._refresh()
            for cb in list(self._watchers.values()):
                with contextlib.suppress(Exception):
                    cb()


# ---------------------------------------------------------------------------
# Session Backend
# ---------------------------------------------------------------------------


class KWinSessionBackend(SessionBackend):
    """SessionBackend for KDE Plasma 6 native Wayland.

    Provides native Wayland layer-shell positioning and KWin workspace
    tracking.  Window listing is not available because KWin 6 does not
    expose a public window-list protocol to third-party clients.
    """

    def __init__(
        self,
        *,
        layer_shell: object,
        launcher: Launcher,
        model: DockModel,
    ) -> None:
        from docking.platform.backends.wayland.services import (
            WaylandLayerShellSurfaceService,
        )

        self._surface = WaylandLayerShellSurfaceService(layer_shell=layer_shell)
        self._windows = AtspiWindowService(launcher=launcher, model=model)
        self._workspaces = KWinWorkspaceService()
        self._visibility = ReducedVisibilityService()
        self._previews = ReducedPreviewService()

    @property
    def name(self) -> str:
        return "kwin"

    @property
    def display_server(self) -> DisplayServer:
        return DisplayServer.WAYLAND

    @property
    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            tracks_windows=True,
            tracks_active_window=True,
            tracks_minimized=False,
            tracks_maximized=False,
            tracks_fullscreen=False,
            tracks_window_geometry=True,
            supports_activate=False,
            supports_minimize=False,
            supports_close=False,
            supports_workspace_list=True,
            supports_workspace_switch=True,
            supports_layer_shell=True,
            supports_screen_reservation=True,
            supports_input_region=True,
        )

    @property
    def windows(self) -> WindowService:
        return self._windows

    @property
    def surface(self) -> SurfaceService:
        return self._surface

    @property
    def visibility(self) -> VisibilityService:
        return self._visibility

    @property
    def previews(self) -> PreviewService:
        return self._previews

    @property
    def workspaces(self) -> WorkspaceService | None:
        return self._workspaces

    @property
    def desktop_actions(self) -> DesktopActionService | None:
        return None

    @property
    def screen_capture(self) -> ScreenCaptureService | None:
        return None

    @property
    def idle(self) -> IdleService | None:
        return None

    @property
    def window_picker(self) -> WindowPickService | None:
        return None

    def start(self) -> None:
        self._surface.start()
        self._windows.start()
        self._workspaces.start()
        self._visibility.start()
        self._previews.start()

    def stop(self) -> None:
        self._previews.stop()
        self._visibility.stop()
        self._workspaces.stop()
        self._windows.stop()
        self._surface.stop()
