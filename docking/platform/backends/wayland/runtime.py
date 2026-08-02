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

"""PyWayland registry and GLib event-loop integration for native Wayland."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from docking.log import get_logger

if TYPE_CHECKING:
    from docking.platform.backends.wayland.idle import WaylandIdleService
    from docking.platform.backends.wayland.previews import (
        WaylandPreviewHandleTracker,
    )
    from docking.platform.backends.wayland.toplevels import (
        WaylandForeignToplevelWindowService,
    )
    from docking.platform.backends.wayland.treeland import (
        TreelandOverlapAdapter,
        TreelandWindowManagementAdapter,
    )
    from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService

log = get_logger(name="wayland_protocol_runtime")


def _iterable_state(value: object) -> Iterable[object] | None:
    """Narrow raw protocol state payloads before publishing them to services."""
    if isinstance(value, (str, bytes, bytearray)):
        return None
    return value if isinstance(value, Iterable) else None


def _workspace_flags(value: object) -> Iterable[object] | int | None:
    """Narrow raw workspace state/capability payloads to their service contract."""
    if isinstance(value, int):
        return value
    return _iterable_state(value)


@dataclass(frozen=True)
class WaylandProtocolFactories:
    """Runtime imports and constructors needed by WaylandProtocolRuntime."""

    display_cls: type
    manager_cls: type
    workspace_manager_cls: type
    glib: object


@dataclass(frozen=True)
class WaylandProtocolProfile:
    """Compositor-specific protocol adapters enabled for one session."""

    hyprland_previews: bool = False
    phoc_previews: bool = False
    cosmic: bool = False
    treeland: bool = False


STANDARD_PROTOCOL_PROFILE = WaylandProtocolProfile()
GENERIC_LAYER_SHELL_PROTOCOL_PROFILE = WaylandProtocolProfile(phoc_previews=True)
HYPRLAND_PROTOCOL_PROFILE = WaylandProtocolProfile(hyprland_previews=True)
COSMIC_PROTOCOL_PROFILE = WaylandProtocolProfile(cosmic=True)
TREELAND_PROTOCOL_PROFILE = WaylandProtocolProfile(treeland=True)

_STANDARD_GLOBAL_INTERFACES = frozenset(
    {
        "zwlr_foreign_toplevel_manager_v1",
        "wl_seat",
        "ext_workspace_manager_v1",
        "ext_foreign_toplevel_list_v1",
        "ext_foreign_toplevel_image_capture_source_manager_v1",
        "ext_image_copy_capture_manager_v1",
        "wl_shm",
        "ext_idle_notifier_v1",
    }
)


class ForeignToplevelProtocolAdapter:
    """Adapter from generated foreign-toplevel callbacks to Docking service."""

    def __init__(self) -> None:
        self._manager = None
        self._seat = None
        self._flush: Callable[[], None] | None = None
        self._service: WaylandForeignToplevelWindowService | None = None
        self._pending_toplevels: list[object] = []
        self._pending_data: dict[object, dict[str, object]] = {}
        self.available = False

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.wlr_foreign_toplevel_management_unstable_v1 import (  # noqa: E501
            ZwlrForeignToplevelManagerV1,
        )

        bind_version = min(version, ZwlrForeignToplevelManagerV1.version)
        self._manager = registry.bind(
            name,
            ZwlrForeignToplevelManagerV1,
            bind_version,
        )
        self._manager.dispatcher["toplevel"] = self._on_toplevel
        self._manager.dispatcher["finished"] = self._on_finished
        self.available = True

    def bind_seat(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.wayland import WlSeat

        self._seat = registry.bind(name, WlSeat, min(version, WlSeat.version))

    def start(self, service: WaylandForeignToplevelWindowService) -> None:
        self._service = service
        for toplevel in tuple(self._pending_toplevels):
            service.toplevel_created(toplevel)
            data = self._pending_data.get(toplevel, {})
            if "title" in data:
                service.title_changed(toplevel, str(data["title"]))
            if "app_id" in data:
                service.app_id_changed(toplevel, str(data["app_id"]))
            if "state" in data:
                states = _iterable_state(data["state"])
                if states is not None:
                    service.state_changed(toplevel, states)
            if data.get("done"):
                service.done(toplevel)

    def stop(self) -> None:
        manager = self._manager
        if manager is not None:
            stop = getattr(manager, "stop", None)
            if callable(stop):
                stop()
        self._manager = None
        self._seat = None
        self._flush = None
        self._service = None
        self._pending_toplevels.clear()
        self._pending_data.clear()
        self.available = False

    def supports_action(self, action: str, handle: object) -> bool:
        method = getattr(handle, action, None)
        if not callable(method):
            return False
        if action == "activate":
            return self._seat is not None
        return True

    def activate(self, handle: object) -> None:
        method = getattr(handle, "activate", None)
        if callable(method) and self._seat is not None:
            method(self._seat)
            self._flush_pending()

    def close(self, handle: object) -> None:
        method = getattr(handle, "close", None)
        if callable(method):
            method()
            self._flush_pending()

    def set_minimized(self, handle: object) -> None:
        method = getattr(handle, "set_minimized", None)
        if callable(method):
            method()
            self._flush_pending()

    def _flush_pending(self) -> None:
        if self._flush is not None:
            self._flush()

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
        toplevel.dispatcher["state"] = lambda _handle, states: self._on_toplevel_state(
            toplevel, states
        )
        toplevel.dispatcher["output_enter"] = lambda _handle, output: (
            self._service.output_entered(toplevel, output)
            if self._service is not None
            else None
        )
        toplevel.dispatcher["output_leave"] = lambda _handle, output: (
            self._service.output_left(toplevel, output)
            if self._service is not None
            else None
        )
        toplevel.dispatcher["parent"] = lambda _handle, parent: (
            self._service.parent_changed(toplevel, parent)
            if self._service is not None
            else None
        )
        toplevel.dispatcher["done"] = lambda _handle: self._on_toplevel_done(toplevel)
        toplevel.dispatcher["closed"] = lambda _handle: self._on_toplevel_closed(
            toplevel
        )

    def _on_toplevel_title(self, toplevel: object, title: str) -> None:
        self._pending_data.setdefault(toplevel, {})["title"] = title
        if self._service is not None:
            self._service.title_changed(toplevel, title)

    def _on_toplevel_app_id(self, toplevel: object, app_id: str) -> None:
        self._pending_data.setdefault(toplevel, {})["app_id"] = app_id
        if self._service is not None:
            self._service.app_id_changed(toplevel, app_id)

    def _on_toplevel_state(self, toplevel: object, states: Iterable[object]) -> None:
        self._pending_data.setdefault(toplevel, {})["state"] = states
        if self._service is not None:
            self._service.state_changed(toplevel, states)

    def _on_toplevel_done(self, toplevel: object) -> None:
        self._pending_data.setdefault(toplevel, {})["done"] = True
        if self._service is not None:
            self._service.done(toplevel)

    def _on_toplevel_closed(self, toplevel: object) -> None:
        if toplevel in self._pending_toplevels:
            self._pending_toplevels.remove(toplevel)
        self._pending_data.pop(toplevel, None)
        if self._service is not None:
            self._service.closed(toplevel)

    def _on_finished(self, manager) -> None:
        self.available = False


class WorkspaceProtocolAdapter:
    """Adapter from ext-workspace callbacks to Docking service."""

    def __init__(self) -> None:
        self._manager = None
        self._flush: Callable[[], None] | None = None
        self._service: WaylandWorkspaceService | None = None
        self._pending_workspaces: list[object] = []
        self._pending_data: dict[object, dict[str, object]] = {}
        self._pending_done = False
        self.available = False

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.ext_workspace_v1 import (
            ExtWorkspaceManagerV1,
        )

        bind_version = min(version, ExtWorkspaceManagerV1.version)
        self._manager = registry.bind(name, ExtWorkspaceManagerV1, bind_version)
        self._manager.dispatcher["workspace"] = self._on_workspace
        self._manager.dispatcher["done"] = self._on_done
        self._manager.dispatcher["finished"] = self._on_finished
        self.available = True

    def start(self, service: WaylandWorkspaceService) -> None:
        self._service = service
        for workspace in tuple(self._pending_workspaces):
            service.workspace_created(workspace)
            data = self._pending_data.get(workspace, {})
            if "id" in data:
                service.id_changed(workspace, str(data["id"]))
            if "name" in data:
                service.name_changed(workspace, str(data["name"]))
            if "capabilities" in data:
                capabilities = _workspace_flags(data["capabilities"])
                if capabilities is not None:
                    service.capabilities_changed(workspace, capabilities)
            if "state" in data:
                states = _workspace_flags(data["state"])
                if states is not None:
                    service.state_changed(workspace, states)
        if self._pending_done:
            service.done()

    def stop(self) -> None:
        manager = self._manager
        if manager is not None:
            stop = getattr(manager, "stop", None)
            if callable(stop):
                stop()
        self._manager = None
        self._flush = None
        self._service = None
        self._pending_workspaces.clear()
        self._pending_data.clear()
        self._pending_done = False
        self.available = False

    def activate(self, handle: object) -> None:
        method = getattr(handle, "activate", None)
        if callable(method):
            method()
        manager = self._manager
        commit = getattr(manager, "commit", None)
        if callable(commit):
            commit()
        if self._flush is not None:
            self._flush()

    def _on_workspace(self, manager, workspace) -> None:
        if workspace not in self._pending_workspaces:
            self._pending_workspaces.append(workspace)
        self._pending_data.setdefault(workspace, {})
        service = self._service
        if service is not None:
            service.workspace_created(workspace)
        workspace.dispatcher["id"] = lambda _handle, value: self._on_workspace_id(
            workspace,
            value,
        )
        workspace.dispatcher["name"] = lambda _handle, value: self._on_workspace_name(
            workspace,
            value,
        )
        workspace.dispatcher["state"] = lambda _handle, states: (
            self._on_workspace_state(workspace, states)
        )
        workspace.dispatcher["capabilities"] = lambda _handle, capabilities: (
            self._on_workspace_capabilities(
                workspace,
                capabilities,
            )
        )
        workspace.dispatcher["removed"] = lambda _handle: self._on_workspace_removed(
            workspace
        )

    def _on_workspace_id(self, workspace: object, value: str) -> None:
        self._pending_data.setdefault(workspace, {})["id"] = value
        if self._service is not None:
            self._service.id_changed(workspace, value)

    def _on_workspace_name(self, workspace: object, value: str) -> None:
        self._pending_data.setdefault(workspace, {})["name"] = value
        if self._service is not None:
            self._service.name_changed(workspace, value)

    def _on_workspace_state(
        self, workspace: object, states: Iterable[object] | int
    ) -> None:
        self._pending_data.setdefault(workspace, {})["state"] = states
        if self._service is not None:
            self._service.state_changed(workspace, states)

    def _on_workspace_capabilities(
        self, workspace: object, capabilities: Iterable[object] | int
    ) -> None:
        self._pending_data.setdefault(workspace, {})["capabilities"] = capabilities
        if self._service is not None:
            self._service.capabilities_changed(workspace, capabilities)

    def _on_workspace_removed(self, workspace: object) -> None:
        if workspace in self._pending_workspaces:
            self._pending_workspaces.remove(workspace)
        self._pending_data.pop(workspace, None)
        if self._service is not None:
            self._service.removed(workspace)

    def _on_done(self, manager) -> None:
        self._pending_done = True
        if self._service is not None:
            self._service.done()

    def _on_finished(self, manager) -> None:
        self.available = False


class IdleProtocolAdapter:
    """Adapter for ext-idle-notify-v1 idle state events."""

    def __init__(self) -> None:
        self._notifier = None
        self._notifier_version = 0
        self._seat = None
        self._notification = None
        self._flush: Callable[[], None] | None = None
        self._service: WaylandIdleService | None = None
        self.available = False

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.ext_idle_notify_v1 import ExtIdleNotifierV1

        bind_version = min(version, ExtIdleNotifierV1.version)
        self._notifier = registry.bind(name, ExtIdleNotifierV1, bind_version)
        self._notifier_version = bind_version
        self.available = True
        self._maybe_create_notification()

    def bind_seat(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.wayland import WlSeat

        self._seat = registry.bind(name, WlSeat, min(version, WlSeat.version))
        self._maybe_create_notification()

    def start(self, service: WaylandIdleService) -> None:
        self._service = service
        self._maybe_create_notification()

    def stop(self) -> None:
        notification = self._notification
        if notification is not None:
            destroy = getattr(notification, "destroy", None)
            if callable(destroy):
                destroy()
        notifier = self._notifier
        if notifier is not None:
            destroy = getattr(notifier, "destroy", None)
            if callable(destroy):
                destroy()
        self._notifier = None
        self._notifier_version = 0
        self._seat = None
        self._notification = None
        self._flush = None
        self._service = None
        self.available = False

    def _maybe_create_notification(self) -> None:
        if (
            self._notification is not None
            or self._notifier is None
            or self._seat is None
            or self._service is None
        ):
            return
        create = None
        if self._notifier_version >= 2:
            create = getattr(self._notifier, "get_input_idle_notification", None)
        if not callable(create):
            create = getattr(self._notifier, "get_idle_notification", None)
        if not callable(create):
            return
        notification = create(0, self._seat)
        notification.dispatcher["idled"] = lambda _notification: self._on_idled()
        notification.dispatcher["resumed"] = lambda _notification: self._on_resumed()
        self._notification = notification
        if self._flush is not None:
            self._flush()

    def _on_idled(self) -> None:
        if self._service is not None:
            self._service.idled()

    def _on_resumed(self) -> None:
        if self._service is not None:
            self._service.resumed()


class PreviewProtocolAdapter:
    """Adapter for generic Wayland toplevel preview capture protocols."""

    def __init__(self) -> None:
        self._toplevel_list = None
        self._source_manager = None
        self._capture_manager = None
        self._shm = None
        self._flush: Callable[[], None] | None = None
        self._tracker: WaylandPreviewHandleTracker | None = None
        self._pending_toplevels: list[object] = []
        self._pending_data: dict[object, dict[str, object]] = {}
        self._shm_formats: set[int] = set()
        self.available = False

    @property
    def capture_available(self) -> bool:
        return (
            self.available
            and self._source_manager is not None
            and self._capture_manager is not None
            and self._shm is not None
        )

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind_toplevel_list(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.ext_foreign_toplevel_list_v1 import (
            ExtForeignToplevelListV1,
        )

        bind_version = min(version, ExtForeignToplevelListV1.version)
        self._toplevel_list = registry.bind(
            name,
            ExtForeignToplevelListV1,
            bind_version,
        )
        self._toplevel_list.dispatcher["toplevel"] = self._on_toplevel
        self._toplevel_list.dispatcher["finished"] = self._on_finished
        self.available = True

    def bind_source_manager(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.ext_image_capture_source_v1 import (
            ExtForeignToplevelImageCaptureSourceManagerV1,
        )

        bind_version = min(
            version,
            ExtForeignToplevelImageCaptureSourceManagerV1.version,
        )
        self._source_manager = registry.bind(
            name,
            ExtForeignToplevelImageCaptureSourceManagerV1,
            bind_version,
        )

    def bind_capture_manager(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.ext_image_copy_capture_v1 import (
            ExtImageCopyCaptureManagerV1,
        )

        bind_version = min(version, ExtImageCopyCaptureManagerV1.version)
        self._capture_manager = registry.bind(
            name,
            ExtImageCopyCaptureManagerV1,
            bind_version,
        )

    def bind_shm(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.wayland import WlShm

        self._shm = registry.bind(name, WlShm, min(version, WlShm.version))
        self._shm.dispatcher["format"] = self._on_shm_format

    def start(self, tracker: WaylandPreviewHandleTracker) -> None:
        self._tracker = tracker
        for toplevel in tuple(self._pending_toplevels):
            tracker.toplevel_created(toplevel)
            data = self._pending_data.get(toplevel, {})
            if "title" in data:
                tracker.title_changed(toplevel, str(data["title"]))
            if "app_id" in data:
                tracker.app_id_changed(toplevel, str(data["app_id"]))
            if "identifier" in data:
                tracker.identifier_changed(toplevel, str(data["identifier"]))
            if data.get("done"):
                tracker.done(toplevel)

    def stop(self) -> None:
        list_manager = self._toplevel_list
        if list_manager is not None:
            stop = getattr(list_manager, "stop", None)
            if callable(stop):
                stop()
        for manager in (self._source_manager, self._capture_manager, self._shm):
            destroy = getattr(manager, "destroy", None)
            if callable(destroy):
                destroy()
        self._toplevel_list = None
        self._source_manager = None
        self._capture_manager = None
        self._shm = None
        self._flush = None
        self._tracker = None
        self._pending_toplevels.clear()
        self._pending_data.clear()
        self._shm_formats.clear()
        self.available = False

    def create_source(self, handle: object) -> object:
        if not self.capture_available or self._source_manager is None:
            raise RuntimeError("Wayland preview capture is unavailable")
        return self._source_manager.create_source(handle)

    def create_session(self, source: object) -> object:
        if not self.capture_available or self._capture_manager is None:
            raise RuntimeError("Wayland preview capture is unavailable")
        return self._capture_manager.create_session(source, 0)

    def create_shm_pool(self, fd: int, size: int) -> object:
        if not self.capture_available or self._shm is None:
            raise RuntimeError("Wayland preview shm is unavailable")
        return self._shm.create_pool(fd, size)

    def flush(self) -> None:
        if self._flush is not None:
            self._flush()

    def _on_toplevel(self, manager, toplevel) -> None:
        if toplevel not in self._pending_toplevels:
            self._pending_toplevels.append(toplevel)
        self._pending_data.setdefault(toplevel, {})
        if self._tracker is not None:
            self._tracker.toplevel_created(toplevel)
        toplevel.dispatcher["title"] = lambda _handle, title: self._on_toplevel_title(
            toplevel,
            title,
        )
        toplevel.dispatcher["app_id"] = lambda _handle, app_id: (
            self._on_toplevel_app_id(toplevel, app_id)
        )
        toplevel.dispatcher["identifier"] = lambda _handle, identifier: (
            self._on_toplevel_identifier(toplevel, identifier)
        )
        toplevel.dispatcher["done"] = lambda _handle: self._on_toplevel_done(toplevel)
        toplevel.dispatcher["closed"] = lambda _handle: self._on_toplevel_closed(
            toplevel
        )

    def _on_toplevel_title(self, toplevel: object, title: str) -> None:
        self._pending_data.setdefault(toplevel, {})["title"] = title
        if self._tracker is not None:
            self._tracker.title_changed(toplevel, title)

    def _on_toplevel_app_id(self, toplevel: object, app_id: str) -> None:
        self._pending_data.setdefault(toplevel, {})["app_id"] = app_id
        if self._tracker is not None:
            self._tracker.app_id_changed(toplevel, app_id)

    def _on_toplevel_identifier(self, toplevel: object, identifier: str) -> None:
        self._pending_data.setdefault(toplevel, {})["identifier"] = identifier
        if self._tracker is not None:
            self._tracker.identifier_changed(toplevel, identifier)

    def _on_toplevel_done(self, toplevel: object) -> None:
        self._pending_data.setdefault(toplevel, {})["done"] = True
        if self._tracker is not None:
            self._tracker.done(toplevel)

    def _on_toplevel_closed(self, toplevel: object) -> None:
        if toplevel in self._pending_toplevels:
            self._pending_toplevels.remove(toplevel)
        self._pending_data.pop(toplevel, None)
        if self._tracker is not None:
            self._tracker.closed(toplevel)

    def _on_finished(self, manager) -> None:
        self.available = False

    def _on_shm_format(self, _shm, format_: int) -> None:
        self._shm_formats.add(int(format_))


class HyprlandPreviewProtocolAdapter:
    """Adapter for Hyprland's toplevel export preview protocol."""

    def __init__(self) -> None:
        self._manager = None
        self._shm = None
        self._flush: Callable[[], None] | None = None
        self.available = False

    @property
    def capture_available(self) -> bool:
        return self.available and self._manager is not None and self._shm is not None

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind_export_manager(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.hyprland_toplevel_export_v1 import (  # noqa: E501
            HyprlandToplevelExportManagerV1,
        )

        bind_version = min(version, HyprlandToplevelExportManagerV1.version)
        self._manager = registry.bind(
            name,
            HyprlandToplevelExportManagerV1,
            bind_version,
        )
        self.available = bind_version >= 2

    def bind_shm(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.wayland import WlShm

        self._shm = registry.bind(name, WlShm, min(version, WlShm.version))

    def stop(self) -> None:
        for obj in (self._manager, self._shm):
            destroy = getattr(obj, "destroy", None)
            if callable(destroy):
                destroy()
        self._manager = None
        self._shm = None
        self._flush = None
        self.available = False

    def create_frame(self, handle: object) -> object:
        if not self.capture_available or self._manager is None:
            raise RuntimeError("Hyprland preview capture is unavailable")
        return self._manager.capture_toplevel_with_wlr_toplevel_handle(0, handle)

    def create_shm_pool(self, fd: int, size: int) -> object:
        if not self.capture_available or self._shm is None:
            raise RuntimeError("Hyprland preview shm is unavailable")
        return self._shm.create_pool(fd, size)

    def flush(self) -> None:
        if self._flush is not None:
            self._flush()


class PhocPreviewProtocolAdapter:
    """Adapter for phoc's window-specific phosh_private thumbnail request."""

    def __init__(self) -> None:
        self._manager = None
        self._shm = None
        self._flush: Callable[[], None] | None = None
        self.available = False

    @property
    def capture_available(self) -> bool:
        return self.available and self._manager is not None and self._shm is not None

    def set_flush_callback(self, callback: Callable[[], None] | None) -> None:
        self._flush = callback

    def bind(self, *, registry, name: int, version: int) -> None:
        from docking.platform.backends.wayland.protocols.phosh_private import (
            PhoshPrivate,
        )

        bind_version = min(version, PhoshPrivate.version)
        self._manager = registry.bind(name, PhoshPrivate, bind_version)
        self.available = bind_version >= 4

    def bind_shm(self, *, registry, name: int, version: int) -> None:
        from pywayland.protocol.wayland import WlShm

        self._shm = registry.bind(name, WlShm, min(version, WlShm.version))

    def create_frame(self, handle: object, width: int, height: int) -> object:
        if not self.capture_available or self._manager is None:
            raise RuntimeError("Phoc preview capture is unavailable")
        return self._manager.get_thumbnail(handle, width, height)

    def create_shm_pool(self, fd: int, size: int) -> object:
        if not self.capture_available or self._shm is None:
            raise RuntimeError("Phoc preview shm is unavailable")
        return self._shm.create_pool(fd, size)

    def flush(self) -> None:
        if self._flush is not None:
            self._flush()

    def stop(self) -> None:
        for obj in (self._manager, self._shm):
            destroy = getattr(obj, "destroy", None)
            if callable(destroy):
                destroy()
        self._manager = None
        self._shm = None
        self._flush = None
        self.available = False


class WaylandProtocolRuntime:
    """Owns direct Wayland protocol connection and event-loop integration."""

    def __init__(
        self,
        *,
        factories: WaylandProtocolFactories | None = None,
        profile: WaylandProtocolProfile = GENERIC_LAYER_SHELL_PROTOCOL_PROFILE,
        foreign_adapter: ForeignToplevelProtocolAdapter | None = None,
        workspace_adapter: WorkspaceProtocolAdapter | None = None,
        preview_adapter: PreviewProtocolAdapter | None = None,
        hyprland_preview_adapter: HyprlandPreviewProtocolAdapter | None = None,
        phoc_preview_adapter: PhocPreviewProtocolAdapter | None = None,
        idle_adapter: IdleProtocolAdapter | None = None,
        cosmic_toplevel_adapter: object | None = None,
        cosmic_overlap_adapter: object | None = None,
    ) -> None:
        self._factories = factories
        self._profile = profile
        self.foreign_toplevel = foreign_adapter or ForeignToplevelProtocolAdapter()
        self.workspaces = workspace_adapter or WorkspaceProtocolAdapter()
        self.previews = preview_adapter or PreviewProtocolAdapter()
        self.hyprland_previews = (
            hyprland_preview_adapter or HyprlandPreviewProtocolAdapter()
            if profile.hyprland_previews
            else None
        )
        self.phoc_previews = (
            phoc_preview_adapter or PhocPreviewProtocolAdapter()
            if profile.phoc_previews
            else None
        )
        self.idle = idle_adapter or IdleProtocolAdapter()
        self.cosmic_toplevel = None
        self.cosmic_overlap = None
        if profile.cosmic:
            from docking.platform.backends.wayland.cosmic import (
                CosmicOverlapAdapter,
                CosmicToplevelAdapter,
            )

            self.cosmic_toplevel = cosmic_toplevel_adapter or CosmicToplevelAdapter()
            self.cosmic_overlap = cosmic_overlap_adapter or CosmicOverlapAdapter()
        self.treeland_overlap = None
        self.treeland_window_management = None
        if profile.treeland:
            from docking.platform.backends.wayland.treeland import (
                TreelandOverlapAdapter,
                TreelandWindowManagementAdapter,
            )

            self.treeland_overlap = TreelandOverlapAdapter()
            self.treeland_window_management = TreelandWindowManagementAdapter()
        self._display = None
        self._registry = None
        self._global_interfaces: dict[int, str] = {}
        self._glib_source_id = 0
        self._running = False

    @property
    def foreign_toplevel_protocol(self) -> object | None:
        return self.foreign_toplevel if self.foreign_toplevel.available else None

    @property
    def workspace_protocol(self) -> object | None:
        return self.workspaces if self.workspaces.available else None

    @property
    def preview_protocol(self) -> object | None:
        return self.previews if self.previews.capture_available else None

    @property
    def hyprland_preview_protocol(self) -> object | None:
        return (
            self.hyprland_previews
            if self.hyprland_previews is not None
            and self.hyprland_previews.capture_available
            else None
        )

    @property
    def phoc_preview_protocol(self) -> object | None:
        return (
            self.phoc_previews
            if self.phoc_previews is not None and self.phoc_previews.capture_available
            else None
        )

    @property
    def idle_protocol(self) -> object | None:
        return self.idle if self.idle.available else None

    @property
    def cosmic_toplevel_protocol(self) -> object | None:
        return (
            self.cosmic_toplevel
            if self.cosmic_toplevel is not None and self.cosmic_toplevel.available
            else None
        )

    @property
    def cosmic_overlap_protocol(self) -> object | None:
        return (
            self.cosmic_overlap
            if self.cosmic_overlap is not None and self.cosmic_overlap.available
            else None
        )

    @property
    def treeland_overlap_protocol(self) -> TreelandOverlapAdapter | None:
        return (
            self.treeland_overlap
            if self.treeland_overlap is not None and self.treeland_overlap.available
            else None
        )

    @property
    def treeland_window_management_protocol(
        self,
    ) -> TreelandWindowManagementAdapter | None:
        return (
            self.treeland_window_management
            if self.treeland_window_management is not None
            and self.treeland_window_management.available
            else None
        )

    def start(self) -> bool:
        if self._running:
            return True
        factories = self._factories or load_protocol_factories()
        if factories is None:
            log.info("Wayland protocol runtime unavailable: pywayland not installed")
            return False
        try:
            display = factories.display_cls()
            display.connect()
            registry = display.get_registry()
            registry.dispatcher["global"] = self._on_global
            registry.dispatcher["global_remove"] = self._on_global_remove
            self._display = display
            self._registry = registry
            self.foreign_toplevel.set_flush_callback(display.flush)
            self.workspaces.set_flush_callback(display.flush)
            self.previews.set_flush_callback(display.flush)
            if self.hyprland_previews is not None:
                self.hyprland_previews.set_flush_callback(display.flush)
            if self.phoc_previews is not None:
                self.phoc_previews.set_flush_callback(display.flush)
            self.idle.set_flush_callback(display.flush)
            if self.cosmic_toplevel is not None:
                self.cosmic_toplevel.set_flush_callback(display.flush)
            if self.cosmic_overlap is not None:
                self.cosmic_overlap.set_flush_callback(display.flush)
            if self.treeland_overlap is not None:
                self.treeland_overlap.set_flush_callback(display.flush)
            if self.treeland_window_management is not None:
                self.treeland_window_management.set_flush_callback(display.flush)
            display.dispatch(block=False)
            display.roundtrip()
            # The roundtrip discovers globals and binds protocol managers. Flush
            # those bind requests so compositors can send initial manager state
            # through the GLib watch without blocking GTK on another roundtrip.
            display.flush()
            self._install_glib_watch(factories=factories, display=display)
            self._running = True
            log.info(
                "Wayland protocol runtime started: foreign_toplevel=%s workspaces=%s "
                "cosmic_toplevel=%s cosmic_overlap=%s",
                self.foreign_toplevel.available,
                self.workspaces.available,
                self.cosmic_toplevel is not None and self.cosmic_toplevel.available,
                self.cosmic_overlap is not None and self.cosmic_overlap.available,
            )
            return True
        except Exception as exc:
            log.info("Wayland protocol runtime unavailable: %s", exc)
            self.stop()
            return False

    def stop(self) -> None:
        self.foreign_toplevel.stop()
        self.workspaces.stop()
        self.previews.stop()
        if self.hyprland_previews is not None:
            self.hyprland_previews.stop()
        if self.phoc_previews is not None:
            self.phoc_previews.stop()
        self.idle.stop()
        if self.cosmic_toplevel is not None:
            self.cosmic_toplevel.stop()
        if self.cosmic_overlap is not None:
            self.cosmic_overlap.stop()
        if self.treeland_overlap is not None:
            self.treeland_overlap.stop()
        if self.treeland_window_management is not None:
            self.treeland_window_management.stop()
        source_id = self._glib_source_id
        factories = self._factories or load_protocol_factories()
        if source_id and factories is not None:
            factories.glib.source_remove(source_id)
        self._glib_source_id = 0
        display = self._display
        if display is not None:
            disconnect = getattr(display, "disconnect", None)
            if callable(disconnect):
                disconnect()
        self._display = None
        self._registry = None
        self._global_interfaces.clear()
        self._running = False

    def _on_global(self, registry, name: int, interface: str, version: int) -> None:
        if self._tracks_global(interface):
            self._global_interfaces[name] = interface
        if interface == "zwlr_foreign_toplevel_manager_v1":
            self.foreign_toplevel.bind(registry=registry, name=name, version=version)
        elif interface == "wl_seat":
            self.foreign_toplevel.bind_seat(
                registry=registry,
                name=name,
                version=version,
            )
            if self.cosmic_toplevel is not None:
                self.cosmic_toplevel.bind_seat(
                    registry=registry,
                    name=name,
                    version=version,
                )
            self.idle.bind_seat(
                registry=registry,
                name=name,
                version=version,
            )
        elif interface == "wl_output":
            if self.treeland_overlap is not None:
                self.treeland_overlap.bind_output(
                    registry=registry,
                    name=name,
                    version=version,
                )
        elif interface == "ext_workspace_manager_v1":
            self.workspaces.bind(registry=registry, name=name, version=version)
        elif interface == "ext_foreign_toplevel_list_v1":
            self.previews.bind_toplevel_list(
                registry=registry,
                name=name,
                version=version,
            )
            if self.cosmic_toplevel is not None:
                self.cosmic_toplevel.bind_toplevel_list(
                    registry=registry,
                    name=name,
                    version=version,
                )
        elif (
            interface == "zcosmic_toplevel_info_v1" and self.cosmic_toplevel is not None
        ):
            self.cosmic_toplevel.bind_toplevel_info(
                registry=registry,
                name=name,
                version=version,
            )
        elif (
            interface == "zcosmic_toplevel_manager_v1"
            and self.cosmic_toplevel is not None
        ):
            self.cosmic_toplevel.bind_toplevel_manager(
                registry=registry,
                name=name,
                version=version,
            )
        elif (
            interface == "zcosmic_overlap_notify_v1" and self.cosmic_overlap is not None
        ):
            self.cosmic_overlap.bind(
                registry=registry,
                name=name,
                version=version,
            )
        elif (
            interface == "treeland_dde_shell_manager_v1"
            and self.treeland_overlap is not None
        ):
            self.treeland_overlap.bind(
                registry=registry,
                name=name,
                version=version,
            )
        elif (
            interface == "treeland_window_management_v1"
            and self.treeland_window_management is not None
        ):
            self.treeland_window_management.bind(
                registry=registry,
                name=name,
                version=version,
            )
        elif interface == "ext_foreign_toplevel_image_capture_source_manager_v1":
            self.previews.bind_source_manager(
                registry=registry,
                name=name,
                version=version,
            )
        elif interface == "ext_image_copy_capture_manager_v1":
            self.previews.bind_capture_manager(
                registry=registry,
                name=name,
                version=version,
            )
        elif interface == "wl_shm":
            self.previews.bind_shm(registry=registry, name=name, version=version)
            if self.hyprland_previews is not None:
                self.hyprland_previews.bind_shm(
                    registry=registry,
                    name=name,
                    version=version,
                )
            if self.phoc_previews is not None:
                self.phoc_previews.bind_shm(
                    registry=registry,
                    name=name,
                    version=version,
                )
        elif (
            interface == "hyprland_toplevel_export_manager_v1"
            and self.hyprland_previews is not None
        ):
            self.hyprland_previews.bind_export_manager(
                registry=registry,
                name=name,
                version=version,
            )
        elif interface == "phosh_private" and self.phoc_previews is not None:
            self.phoc_previews.bind(
                registry=registry,
                name=name,
                version=version,
            )
        elif interface == "ext_idle_notifier_v1":
            self.idle.bind(registry=registry, name=name, version=version)

    def _tracks_global(self, interface: str) -> bool:
        if interface in _STANDARD_GLOBAL_INTERFACES:
            return True
        if (
            self.hyprland_previews is not None
            and interface == "hyprland_toplevel_export_manager_v1"
        ):
            return True
        if self.phoc_previews is not None and interface == "phosh_private":
            return True
        if self.cosmic_toplevel is not None and interface in {
            "zcosmic_toplevel_info_v1",
            "zcosmic_toplevel_manager_v1",
        }:
            return True
        if self.cosmic_overlap is not None and interface == "zcosmic_overlap_notify_v1":
            return True
        if self.treeland_overlap is not None and interface in {
            "wl_output",
            "treeland_dde_shell_manager_v1",
        }:
            return True
        return (
            self.treeland_window_management is not None
            and interface == "treeland_window_management_v1"
        )

    def _on_global_remove(self, _registry, name: int) -> None:
        interface = self._global_interfaces.pop(name, None)
        if interface == "wl_output" and self.treeland_overlap is not None:
            self.treeland_overlap.unbind_output(name)
            return
        if interface is not None:
            log.info("Wayland global removed, stopping protocol runtime: %s", interface)
            self.stop()

    def _install_glib_watch(
        self, *, factories: WaylandProtocolFactories, display
    ) -> None:
        fd = display.get_fd()
        channel = factories.glib.IOChannel.unix_new(fd)
        self._glib_source_id = channel.add_watch(
            factories.glib.IO_IN | factories.glib.IO_ERR | factories.glib.IO_HUP,
            self._on_wayland_fd,
            display,
        )

    def _on_wayland_fd(self, _channel, condition, display) -> bool:
        factories = self._factories or load_protocol_factories()
        if factories is None:
            return False
        if condition & (factories.glib.IO_ERR | factories.glib.IO_HUP):
            self.stop()
            return False
        try:
            display.read()
            display.dispatch(block=False)
            display.flush()
        except Exception as exc:
            log.info("Wayland protocol runtime dispatch failed: %s", exc)
            self.stop()
            return False
        return True


def load_protocol_factories() -> WaylandProtocolFactories | None:
    """Load optional pywayland and GLib runtime dependencies."""
    try:
        from gi.repository import GLib
        from pywayland.client import Display

        from docking.platform.backends.wayland.protocols.ext_workspace_v1.ext_workspace_manager_v1 import (  # noqa: E501
            ExtWorkspaceManagerV1,
        )
        from docking.platform.backends.wayland.protocols.wlr_foreign_toplevel_management_unstable_v1 import (  # noqa: E501
            ZwlrForeignToplevelManagerV1,
        )
    except (ImportError, ValueError):
        return None
    return WaylandProtocolFactories(
        display_cls=Display,
        manager_cls=ZwlrForeignToplevelManagerV1,
        workspace_manager_cls=ExtWorkspaceManagerV1,
        glib=GLib,
    )
