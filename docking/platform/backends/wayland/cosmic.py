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

"""COSMIC protocol adapters for toplevel info/management and overlap.

These adapters translate raw Wayland protocol events into calls on Docking
service objects (WindowService, WorkspaceService, VisibilityMonitor). They
follow the same adapter pattern as the generic ForeignToplevelProtocolAdapter
but target COSMIC-specific protocol families.

COSMIC protocol composition:

  ext_foreign_toplevel_list_v1  +--- base toplevel listing (title, app_id,
  zcosmic_toplevel_info_v1          done, closed)
  zcosmic_toplevel_manager_v1  +--- window actions (activate, close,
                                     minimize, maximize, fullscreen)

  zcosmic_overlap_notify_v1    +--- overlap-driven dock visibility
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.platform.backends.base import Rect

if TYPE_CHECKING:
    from docking.platform.backends.wayland.toplevels import (
        WaylandForeignToplevelWindowService,
    )

log = get_logger(name="cosmic_protocols")


# ---------------------------------------------------------------------------
# Toplevel state constants (matching xdg-toplevel states)
# ---------------------------------------------------------------------------
_STATE_BY_VALUE = {
    0: "maximized",
    1: "minimized",
    2: "activated",
    3: "fullscreen",
    4: "sticky",
}

# Management capability constants
_CAP_CLOSE = 1
_CAP_ACTIVATE = 2
_CAP_MAXIMIZE = 3
_CAP_MINIMIZE = 4
_CAP_FULLSCREEN = 5
_CAP_MOVE_TO_WORKSPACE = 6
_CAP_STICKY = 7
_CAP_MOVE_TO_EXT_WORKSPACE = 8

# ---------------------------------------------------------------------------
# COSMIC Toplevel Adapter (info + management)
# ---------------------------------------------------------------------------


class CosmicToplevelAdapter:
    """Binds ext_foreign_toplevel_list_v1 + zcosmic_toplevel_info_v1
    + zcosmic_toplevel_manager_v1 and feeds a WindowService.
    """

    def __init__(self) -> None:
        # Base toplevel list
        self._toplevel_list = None
        # COSMIC info (extended properties like state, geometry, workspace)
        self._toplevel_info = None
        # COSMIC management (activate, close, minimize, etc.)
        self._toplevel_manager = None
        self._seat = None
        self._flush: Callable[[], None] | None = None
        self._service: WaylandForeignToplevelWindowService | None = None

        # Pending toplevel data during initial enumeration
        self._pending_toplevels: list[object] = []
        self._pending_data: dict[object, dict[str, object]] = {}

        # Known COSMIC info handles keyed by ext toplevel handle
        self._cosmic_handles: dict[object, object] = {}
        # Reverse mapping: cosmic handle -> ext handle
        self._ext_handles: dict[object, object] = {}

        # Management capabilities
        self._capabilities: set[int] = set()
        self._dirty_toplevels: set[object] = set()

        self.available = False

    @property
    def has_management(self) -> bool:
        return self._toplevel_manager is not None

    @property
    def capabilities(self) -> frozenset[int]:
        return frozenset(self._capabilities)

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    # -- Registry bindings ---------------------------------------------------

    def bind_toplevel_list(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.ext_foreign_toplevel_list_v1 import (
            ExtForeignToplevelListV1,
        )

        bind_version = min(version, ExtForeignToplevelListV1.version)
        self._toplevel_list = registry.bind(
            name, ExtForeignToplevelListV1, bind_version
        )
        self._toplevel_list.dispatcher["toplevel"] = self._on_toplevel
        self._toplevel_list.dispatcher["finished"] = self._on_finished
        self.available = True

    def bind_toplevel_info(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.cosmic_toplevel_info_v1 import (  # noqa: E501
            ZcosmicToplevelInfoV1,
        )

        bind_version = min(version, ZcosmicToplevelInfoV1.version)
        self._toplevel_info = registry.bind(name, ZcosmicToplevelInfoV1, bind_version)
        if bind_version >= 2:
            self._toplevel_info.dispatcher["done"] = self._on_info_done

    def bind_toplevel_manager(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.cosmic_toplevel_management_v1 import (  # noqa: E501
            ZcosmicToplevelManagerV1,
        )

        bind_version = min(version, ZcosmicToplevelManagerV1.version)
        self._toplevel_manager = registry.bind(
            name, ZcosmicToplevelManagerV1, bind_version
        )
        self._toplevel_manager.dispatcher["capabilities"] = self._on_capabilities

    def bind_seat(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.wayland import WlSeat

        self._seat = registry.bind(name, WlSeat, min(version, WlSeat.version))

    # -- Lifecycle -----------------------------------------------------------

    def start(self, service: WaylandForeignToplevelWindowService) -> None:
        self._service = service
        # Replay pending toplevels
        for toplevel in tuple(self._pending_toplevels):
            service.toplevel_created(toplevel)
            data = self._pending_data.get(toplevel, {})
            if "title" in data:
                service.title_changed(toplevel, str(data["title"]))
            if "app_id" in data:
                service.app_id_changed(toplevel, str(data["app_id"]))
            state = data.get("state")
            if isinstance(state, Iterable):
                service.state_changed(toplevel, state)
            geometry = data.get("geometry")
            if isinstance(geometry, Rect):
                service.geometry_changed(toplevel, geometry)
            workspace = data.get("ext_workspace")
            if workspace is not None:
                service.workspace_changed(toplevel, _workspace_id(workspace))
            if data.get("done"):
                service.done(toplevel)
        # Request COSMIC info extensions for known toplevels (v2+)
        if self._toplevel_info is not None and hasattr(
            self._toplevel_info, "get_cosmic_toplevel"
        ):
            for toplevel in tuple(self._pending_toplevels):
                self._request_cosmic_info(toplevel)

    def stop(self) -> None:
        for manager in (
            self._toplevel_list,
            self._toplevel_info,
            self._toplevel_manager,
        ):
            if manager is not None:
                stop = getattr(manager, "stop", None) or getattr(
                    manager, "destroy", None
                )
                if callable(stop):
                    stop()
        self._toplevel_list = None
        self._toplevel_info = None
        self._toplevel_manager = None
        self._seat = None
        self._flush = None
        self._service = None
        self._pending_toplevels.clear()
        self._pending_data.clear()
        self._cosmic_handles.clear()
        self._ext_handles.clear()
        self._capabilities.clear()
        self._dirty_toplevels.clear()
        self.available = False

    # -- Action checks -------------------------------------------------------

    def supports_action(self, action: str, handle: object) -> bool:
        """Check whether a management action is available for a handle."""
        if self._toplevel_manager is None:
            return False
        if action == "activate":
            return _CAP_ACTIVATE in self._capabilities and self._seat is not None
        if action == "close":
            return _CAP_CLOSE in self._capabilities
        if action == "set_minimized":
            return _CAP_MINIMIZE in self._capabilities
        if action == "set_maximized":
            return _CAP_MAXIMIZE in self._capabilities
        if action == "set_fullscreen":
            return _CAP_FULLSCREEN in self._capabilities
        return False

    # -- Action execution ----------------------------------------------------

    def activate(self, handle: object) -> None:
        if self._toplevel_manager is None or self._seat is None:
            return
        cosmic = self._cosmic_handles.get(handle, handle)
        self._toplevel_manager.activate(cosmic, self._seat)
        self._flush_pending()

    def close(self, handle: object) -> None:
        if self._toplevel_manager is None:
            return
        cosmic = self._cosmic_handles.get(handle, handle)
        self._toplevel_manager.close(cosmic)
        self._flush_pending()

    def set_minimized(self, handle: object) -> None:
        if self._toplevel_manager is None:
            return
        cosmic = self._cosmic_handles.get(handle, handle)
        self._toplevel_manager.set_minimized(cosmic)
        self._flush_pending()

    def unset_minimized(self, handle: object) -> None:
        if self._toplevel_manager is None:
            return
        cosmic = self._cosmic_handles.get(handle, handle)
        self._toplevel_manager.unset_minimized(cosmic)
        self._flush_pending()

    # -- ext_foreign_toplevel_list_v1 events ---------------------------------

    def _on_toplevel(self, manager, toplevel) -> None:
        if toplevel not in self._pending_toplevels:
            self._pending_toplevels.append(toplevel)
        self._pending_data.setdefault(toplevel, {})
        service = self._service
        if service is not None:
            service.toplevel_created(toplevel)

        toplevel.dispatcher["title"] = lambda _handle, title: self._on_toplevel_title(
            toplevel, title
        )
        toplevel.dispatcher["app_id"] = lambda _handle, app_id: (
            self._on_toplevel_app_id(toplevel, app_id)
        )
        toplevel.dispatcher["done"] = lambda _handle: self._on_toplevel_done(toplevel)
        toplevel.dispatcher["closed"] = lambda _handle: self._on_toplevel_closed(
            toplevel
        )

        # Request COSMIC info extension if available
        if service is not None:
            self._request_cosmic_info(toplevel)

    def _on_toplevel_title(self, toplevel: object, title: str) -> None:
        self._pending_data.setdefault(toplevel, {})["title"] = title
        if self._service is not None:
            self._service.title_changed(toplevel, title)

    def _on_toplevel_app_id(self, toplevel: object, app_id: str) -> None:
        self._pending_data.setdefault(toplevel, {})["app_id"] = app_id
        if self._service is not None:
            self._service.app_id_changed(toplevel, app_id)

    def _on_toplevel_done(self, toplevel: object) -> None:
        self._pending_data.setdefault(toplevel, {})["done"] = True
        if self._service is not None:
            self._service.done(toplevel)

    def _on_toplevel_closed(self, toplevel: object) -> None:
        if toplevel in self._pending_toplevels:
            self._pending_toplevels.remove(toplevel)
        self._pending_data.pop(toplevel, None)
        cosmic_handle = self._cosmic_handles.pop(toplevel, None)
        if cosmic_handle is not None:
            self._ext_handles.pop(cosmic_handle, None)
        self._dirty_toplevels.discard(toplevel)
        if self._service is not None:
            self._service.closed(toplevel)

    def _on_finished(self, manager) -> None:
        self.available = False

    # -- zcosmic_toplevel_info_v1 events -------------------------------------

    def _on_info_done(self, manager) -> None:
        service = self._service
        if service is None:
            return
        for toplevel in tuple(self._dirty_toplevels):
            service.done(toplevel)
        self._dirty_toplevels.clear()

    # -- zcosmic_toplevel_manager_v1 events ----------------------------------

    def _on_capabilities(self, manager, caps) -> None:
        caps_bytes = _array_to_bytes(caps)
        self._capabilities = {val[0] for val in struct.iter_unpack("I", caps_bytes)}
        log.info("COSMIC toplevel management capabilities: %s", self._capabilities)

    # -- Internal helpers ----------------------------------------------------

    def _request_cosmic_info(self, toplevel: object) -> None:
        """Request COSMIC info extension for an ext_foreign_toplevel_handle."""
        if self._toplevel_info is None:
            return
        get_info = getattr(self._toplevel_info, "get_cosmic_toplevel", None)
        if not callable(get_info):
            return
        try:
            cosmic_handle = get_info(toplevel)
        except Exception:
            return
        self._cosmic_handles[toplevel] = cosmic_handle
        self._ext_handles[cosmic_handle] = toplevel
        # Wire COSMIC handle events to the service
        service = self._service
        cosmic_handle.dispatcher["state"] = lambda _h, states: self._on_cosmic_state(
            toplevel, states
        )
        cosmic_handle.dispatcher["geometry"] = lambda _h, output, x, y, w, h: (
            self._on_cosmic_geometry(toplevel, output, x, y, w, h)
        )
        cosmic_handle.dispatcher["output_enter"] = lambda _h, output: (
            service.output_entered(toplevel, output) if service is not None else None
        )
        cosmic_handle.dispatcher["output_leave"] = lambda _h, output: (
            service.output_left(toplevel, output) if service is not None else None
        )
        # v3: ext_workspace_enter / ext_workspace_leave
        cosmic_handle.dispatcher["ext_workspace_enter"] = lambda _h, ws: (
            self._on_cosmic_workspace_enter(toplevel, ws)
        )
        cosmic_handle.dispatcher["ext_workspace_leave"] = lambda _h, ws: (
            self._on_cosmic_workspace_leave(toplevel, ws)
        )

    def _on_cosmic_state(self, toplevel: object, states) -> None:
        self._pending_data.setdefault(toplevel, {})["state"] = states
        self._dirty_toplevels.add(toplevel)
        if self._service is not None:
            self._service.state_changed(toplevel, states)

    def _on_cosmic_geometry(
        self,
        toplevel: object,
        output: object,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        geometry = Rect(x=x, y=y, width=width, height=height)
        self._pending_data.setdefault(toplevel, {})["geometry"] = geometry
        if self._service is not None:
            self._service.geometry_changed(toplevel, geometry)

    def _on_cosmic_workspace_enter(self, toplevel: object, workspace) -> None:
        self._pending_data.setdefault(toplevel, {})["ext_workspace"] = workspace
        if self._service is not None:
            self._service.workspace_changed(toplevel, _workspace_id(workspace))

    def _on_cosmic_workspace_leave(self, toplevel: object, workspace) -> None:
        data = self._pending_data.get(toplevel, {})
        if data.get("ext_workspace") is workspace:
            data.pop("ext_workspace", None)
            if self._service is not None:
                self._service.workspace_changed(toplevel, None)

    def _flush_pending(self) -> None:
        if self._flush is not None:
            self._flush()


# ---------------------------------------------------------------------------
# COSMIC Overlap Adapter
# ---------------------------------------------------------------------------


class CosmicOverlapAdapter:
    """Binds zcosmic_overlap_notify_v1 for dodge/autohide overlap detection.

    This adapter creates a notification subscription on a layer-shell surface
    and exposes a simple boolean "overlapped" signal that Docking's
    VisibilityMonitor can consume.
    """

    def __init__(self) -> None:
        self._overlap_notify = None
        self._notification = None
        self._layer_surface = None
        self._flush: Callable[[], None] | None = None
        self._on_change: Callable[[bool], None] | None = None

        # Track overlapping elements
        self._overlapping_toplevels: set[object] = set()
        self._overlapping_layers: set[str] = set()

        self.available = False

    @property
    def is_overlapped(self) -> bool:
        return bool(self._overlapping_toplevels or self._overlapping_layers)

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.cosmic_overlap_notify_v1 import (  # noqa: E501
            ZcosmicOverlapNotifyV1,
        )

        bind_version = min(version, ZcosmicOverlapNotifyV1.version)
        self._overlap_notify = registry.bind(name, ZcosmicOverlapNotifyV1, bind_version)
        self.available = True

    def start(self, layer_surface: object, on_change: Callable[[bool], None]) -> None:
        """Subscribe to overlap notifications on a layer-shell surface."""
        if self._overlap_notify is None:
            return
        self._on_change = on_change
        self._layer_surface = layer_surface
        try:
            self._notification = self._overlap_notify.notify_on_overlap(layer_surface)
        except Exception as exc:
            log.info("COSMIC overlap notify unavailable: %s", exc)
            self.available = False
            return

        self._notification.dispatcher["toplevel_enter"] = self._on_toplevel_enter
        self._notification.dispatcher["toplevel_leave"] = self._on_toplevel_leave
        self._notification.dispatcher["layer_enter"] = self._on_layer_enter
        self._notification.dispatcher["layer_leave"] = self._on_layer_leave

    def stop(self) -> None:
        if self._notification is not None:
            destroy = getattr(self._notification, "destroy", None)
            if callable(destroy):
                destroy()
        self._notification = None
        self._layer_surface = None
        self._on_change = None
        self._overlapping_toplevels.clear()
        self._overlapping_layers.clear()
        if self._overlap_notify is not None:
            self._overlap_notify = None
        self._flush = None
        self.available = False

    def evaluate_now(self) -> None:
        """Force evaluation is a no-op; the compositor pushes events."""

    # -- zcosmic_overlap_notification_v1 events ------------------------------

    def _on_toplevel_enter(
        self,
        toplevel: object,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        was_empty = not self._overlapping_toplevels
        self._overlapping_toplevels.add(toplevel)
        if was_empty and self._on_change is not None:
            self._on_change(True)

    def _on_toplevel_leave(self, toplevel: object) -> None:
        self._overlapping_toplevels.discard(toplevel)
        if (
            not self._overlapping_toplevels
            and not self._overlapping_layers
            and self._on_change is not None
        ):
            self._on_change(False)

    def _on_layer_enter(
        self,
        identifier: str,
        namespace: str,
        exclusive: int,
        layer: int,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> None:
        was_empty = not self._overlapping_layers
        self._overlapping_layers.add(identifier)
        if (
            was_empty
            and not self._overlapping_toplevels
            and self._on_change is not None
        ):
            self._on_change(True)

    def _on_layer_leave(self, identifier: str) -> None:
        self._overlapping_layers.discard(identifier)
        if (
            not self._overlapping_layers
            and not self._overlapping_toplevels
            and self._on_change is not None
        ):
            self._on_change(False)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _array_to_bytes(value) -> bytes:
    """Convert a Wayland array argument to bytes."""
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    if hasattr(value, "data"):
        return bytes(value.data)
    return bytes(value)


def _workspace_id(workspace: object) -> str:
    identifier = getattr(workspace, "id", None)
    if identifier is not None:
        return str(identifier)
    return str(id(workspace))


def _parse_capabilities(caps_bytes: bytes) -> set[int]:
    """Parse COSMIC capabilities array into a set of bit flags."""
    result: set[int] = set()
    for (val,) in struct.iter_unpack("I", caps_bytes):
        result.add(val)
    return result
