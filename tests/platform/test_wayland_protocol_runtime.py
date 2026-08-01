"""Tests for native Wayland protocol runtime scaffolding."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("pywayland")

ExtForeignToplevelListV1 = pytest.importorskip(
    "pywayland.protocol.ext_foreign_toplevel_list_v1"
).ExtForeignToplevelListV1
ExtIdleNotifierV1 = pytest.importorskip(
    "pywayland.protocol.ext_idle_notify_v1"
).ExtIdleNotifierV1
ExtForeignToplevelImageCaptureSourceManagerV1 = pytest.importorskip(
    "pywayland.protocol.ext_image_capture_source_v1"
).ExtForeignToplevelImageCaptureSourceManagerV1
ExtImageCopyCaptureManagerV1 = pytest.importorskip(
    "pywayland.protocol.ext_image_copy_capture_v1"
).ExtImageCopyCaptureManagerV1
from pywayland.protocol.wayland import WlOutput, WlSeat, WlShm

from docking.platform.backends.wayland.idle import WaylandIdleService
from docking.platform.backends.wayland.protocols.ext_workspace_v1.ext_workspace_manager_v1 import (
    ExtWorkspaceManagerV1,
)
from docking.platform.backends.wayland.protocols.hyprland_toplevel_export_v1 import (
    HyprlandToplevelExportManagerV1,
)
from docking.platform.backends.wayland.protocols.phosh_private import PhoshPrivate
from docking.platform.backends.wayland.protocols.treeland_shell import (
    TreelandDDEShellManagerV1,
    TreelandWindowManagementV1,
)
from docking.platform.backends.wayland.protocols.wlr_foreign_toplevel_management_unstable_v1 import (
    ZwlrForeignToplevelManagerV1,
)
from docking.platform.backends.wayland.runtime import (
    ForeignToplevelProtocolAdapter,
    IdleProtocolAdapter,
    PhocPreviewProtocolAdapter,
    PreviewProtocolAdapter,
    WaylandProtocolFactories,
    WaylandProtocolRuntime,
    WorkspaceProtocolAdapter,
)
from docking.platform.backends.wayland.toplevels import (
    WaylandForeignToplevelWindowService,
)
from docking.platform.backends.wayland.workspaces import WaylandWorkspaceService


class FakeProxy:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}
        self.stop = MagicMock()
        self.commit = MagicMock()
        self.destroy = MagicMock()
        self.notifications: list[tuple[int, object, FakeHandle]] = []

    def get_input_idle_notification(self, timeout: int, seat: object) -> FakeHandle:
        notification = FakeHandle()
        self.notifications.append((timeout, seat, notification))
        return notification


class FakeRegistry:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}
        self.bound: list[tuple[int, object, int]] = []
        self.proxies: dict[str, FakeProxy] = {}

    def bind(self, name: int, interface: object, version: int) -> FakeProxy:
        proxy = FakeProxy()
        self.bound.append((name, interface, version))
        self.proxies[interface.name] = proxy
        return proxy


class FakeHandle:
    def __init__(self) -> None:
        self.dispatcher: dict[str, object] = {}


class FakeDisplay:
    registry = FakeRegistry()

    def __init__(self) -> None:
        self.dispatch = MagicMock()
        self.roundtrip = MagicMock()
        self.read = MagicMock()
        self.flush = MagicMock()
        self.disconnect = MagicMock()

    def connect(self) -> None:
        return None

    def get_registry(self) -> FakeRegistry:
        return self.registry

    def get_fd(self) -> int:
        return 123


class DelayedWorkspaceDisplay(FakeDisplay):
    registry = FakeRegistry()

    def __init__(self) -> None:
        self.dispatch = MagicMock()
        self.flush = MagicMock()
        self.disconnect = MagicMock()
        self._workspace_sent = False

    def roundtrip(self):
        self.registry.dispatcher["global"](
            self.registry,
            11,
            "ext_workspace_manager_v1",
            99,
        )

    def read(self):
        if self._workspace_sent:
            return
        self._workspace_sent = True
        manager = self.registry.proxies["ext_workspace_manager_v1"]
        workspace = FakeHandle()
        manager.dispatcher["workspace"](manager, workspace)
        workspace.dispatcher["name"](workspace, "Workspace 1")
        workspace.dispatcher["capabilities"](workspace, 1)
        workspace.dispatcher["state"](workspace, 1)
        manager.dispatcher["done"](manager)


class FakeChannel:
    def __init__(self, fd: int, glib: object) -> None:
        self.fd = fd
        self.glib = glib

    def add_watch(self, condition: int, callback, display) -> int:
        self.glib.watch = (condition, callback, display)
        return 77


class FakeIOChannel:
    def __init__(self, glib: object) -> None:
        self._glib = glib

    def unix_new(self, fd: int) -> FakeChannel:
        return FakeChannel(fd=fd, glib=self._glib)


class FakeGLib:
    IO_IN = 1
    IO_ERR = 2
    IO_HUP = 4

    def __init__(self) -> None:
        self.IOChannel = FakeIOChannel(self)
        self.removed: list[int] = []
        self.watch = None

    def source_remove(self, source_id: int) -> None:
        self.removed.append(source_id)


def _factories(glib: FakeGLib) -> WaylandProtocolFactories:
    FakeDisplay.registry = FakeRegistry()
    return WaylandProtocolFactories(
        display_cls=FakeDisplay,
        manager_cls=ZwlrForeignToplevelManagerV1,
        workspace_manager_cls=ExtWorkspaceManagerV1,
        glib=glib,
    )


def _workspace_factories(glib: FakeGLib) -> WaylandProtocolFactories:
    DelayedWorkspaceDisplay.registry = FakeRegistry()
    return WaylandProtocolFactories(
        display_cls=DelayedWorkspaceDisplay,
        manager_cls=ZwlrForeignToplevelManagerV1,
        workspace_manager_cls=ExtWorkspaceManagerV1,
        glib=glib,
    )


def test_wayland_protocol_runtime_binds_known_globals_and_installs_watch():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_factories(glib))

    assert runtime.start() is True
    registry = FakeDisplay.registry
    registry.dispatcher["global"](
        registry,
        10,
        "zwlr_foreign_toplevel_manager_v1",
        99,
    )
    registry.dispatcher["global"](registry, 11, "ext_workspace_manager_v1", 99)
    registry.dispatcher["global"](registry, 12, "wl_seat", 99)

    assert runtime.foreign_toplevel_protocol is runtime.foreign_toplevel
    assert runtime.workspace_protocol is runtime.workspaces
    assert registry.bound == [
        (10, ZwlrForeignToplevelManagerV1, ZwlrForeignToplevelManagerV1.version),
        (11, ExtWorkspaceManagerV1, ExtWorkspaceManagerV1.version),
        (12, WlSeat, WlSeat.version),
        (12, WlSeat, WlSeat.version),
        (12, WlSeat, WlSeat.version),
    ]
    assert glib.watch is not None


def test_wayland_protocol_runtime_binds_preview_protocol_set():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_factories(glib))

    assert runtime.start() is True
    registry = FakeDisplay.registry
    registry.dispatcher["global"](registry, 20, "ext_foreign_toplevel_list_v1", 99)
    registry.dispatcher["global"](
        registry,
        21,
        "ext_foreign_toplevel_image_capture_source_manager_v1",
        99,
    )
    registry.dispatcher["global"](
        registry,
        22,
        "ext_image_copy_capture_manager_v1",
        99,
    )
    registry.dispatcher["global"](registry, 23, "wl_shm", 99)

    assert runtime.preview_protocol is runtime.previews
    assert registry.bound == [
        (20, ExtForeignToplevelListV1, ExtForeignToplevelListV1.version),
        (20, ExtForeignToplevelListV1, ExtForeignToplevelListV1.version),
        (
            21,
            ExtForeignToplevelImageCaptureSourceManagerV1,
            ExtForeignToplevelImageCaptureSourceManagerV1.version,
        ),
        (22, ExtImageCopyCaptureManagerV1, ExtImageCopyCaptureManagerV1.version),
        (23, WlShm, WlShm.version),
        (23, WlShm, WlShm.version),
        (23, WlShm, WlShm.version),
    ]


def test_wayland_protocol_runtime_binds_hyprland_preview_protocol():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_factories(glib))

    assert runtime.start() is True
    registry = FakeDisplay.registry
    registry.dispatcher["global"](
        registry,
        30,
        "hyprland_toplevel_export_manager_v1",
        99,
    )
    registry.dispatcher["global"](registry, 31, "wl_shm", 99)

    assert runtime.hyprland_preview_protocol is runtime.hyprland_previews
    assert registry.bound == [
        (
            30,
            HyprlandToplevelExportManagerV1,
            HyprlandToplevelExportManagerV1.version,
        ),
        (31, WlShm, WlShm.version),
        (31, WlShm, WlShm.version),
        (31, WlShm, WlShm.version),
    ]


def test_wayland_protocol_runtime_binds_phoc_preview_protocol():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_factories(glib))

    assert runtime.start() is True
    registry = FakeDisplay.registry
    registry.dispatcher["global"](registry, 35, "phosh_private", 7)
    registry.dispatcher["global"](registry, 36, "wl_shm", 99)

    assert runtime.phoc_preview_protocol is runtime.phoc_previews
    assert (35, PhoshPrivate, PhoshPrivate.version) in registry.bound
    assert (36, WlShm, WlShm.version) in registry.bound


def test_phoc_preview_adapter_requires_protocol_v4_and_shm():
    registry = FakeRegistry()
    adapter = PhocPreviewProtocolAdapter()

    adapter.bind(registry=registry, name=1, version=3)
    adapter.bind_shm(registry=registry, name=2, version=1)
    assert adapter.capture_available is False

    adapter.bind(registry=registry, name=3, version=7)
    assert adapter.capture_available is True


def test_wayland_protocol_runtime_binds_idle_protocol():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_factories(glib))

    assert runtime.start() is True
    registry = FakeDisplay.registry
    registry.dispatcher["global"](registry, 40, "ext_idle_notifier_v1", 99)
    registry.dispatcher["global"](registry, 41, "wl_seat", 99)

    assert runtime.idle_protocol is runtime.idle
    assert registry.bound == [
        (40, ExtIdleNotifierV1, ExtIdleNotifierV1.version),
        (41, WlSeat, WlSeat.version),
        (41, WlSeat, WlSeat.version),
        (41, WlSeat, WlSeat.version),
    ]


def test_wayland_protocol_runtime_binds_treeland_extensions_and_outputs():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_factories(glib))

    assert runtime.start() is True
    registry = FakeDisplay.registry
    registry.dispatcher["global"](
        registry,
        50,
        "treeland_dde_shell_manager_v1",
        99,
    )
    registry.dispatcher["global"](
        registry,
        51,
        "treeland_window_management_v1",
        99,
    )
    registry.dispatcher["global"](registry, 52, "wl_output", 99)

    assert runtime.treeland_overlap_protocol is runtime.treeland_overlap
    assert (
        runtime.treeland_window_management_protocol
        is runtime.treeland_window_management
    )
    assert registry.bound == [
        (
            50,
            TreelandDDEShellManagerV1,
            TreelandDDEShellManagerV1.version,
        ),
        (
            51,
            TreelandWindowManagementV1,
            TreelandWindowManagementV1.version,
        ),
        (52, WlOutput, WlOutput.version),
    ]

    registry.dispatcher["global_remove"](registry, 52)
    assert runtime.treeland_overlap._outputs == []


def test_wayland_protocol_runtime_fd_read_receives_initial_workspaces():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_workspace_factories(glib))

    assert runtime.start() is True
    _condition, callback, display = glib.watch
    assert callback(None, glib.IO_IN, display) is True
    service = WaylandWorkspaceService(protocol=runtime.workspaces)
    service.start()

    active = service.active_workspace()
    assert active is not None
    assert active.name == "Workspace 1"


def test_wayland_protocol_runtime_dispatches_and_stops_cleanly():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_factories(glib))
    runtime.start()
    _condition, callback, display = glib.watch

    assert callback(None, glib.IO_IN, display) is True
    display.dispatch.assert_called()
    display.flush.assert_called()

    runtime.stop()

    assert glib.removed == [77]
    display.disconnect.assert_called_once()


def test_wayland_protocol_runtime_handles_fd_error():
    glib = FakeGLib()
    runtime = WaylandProtocolRuntime(factories=_factories(glib))
    runtime.start()
    _condition, callback, display = glib.watch

    assert callback(None, glib.IO_HUP, display) is False
    assert glib.removed == [77]


def test_wayland_protocol_runtime_fails_closed_without_factories():
    runtime = WaylandProtocolRuntime(factories=None)
    runtime._factories = SimpleNamespace()  # invalid factories trigger fallback

    assert runtime.start() is False
    assert runtime.foreign_toplevel_protocol is None
    assert runtime.workspace_protocol is None


def test_workspace_adapter_replays_initial_events_after_service_start():
    adapter = WorkspaceProtocolAdapter()
    workspace = FakeHandle()
    adapter._on_workspace(None, workspace)
    workspace.dispatcher["id"](workspace, "workspace-a")
    workspace.dispatcher["name"](workspace, "Code")
    workspace.dispatcher["capabilities"](workspace, 1)
    workspace.dispatcher["state"](workspace, 1)
    adapter._on_done(None)

    service = WaylandWorkspaceService(protocol=adapter)
    adapter.start(service)

    active = service.active_workspace()
    assert active is not None
    assert active.id == "workspace-a"
    assert active.name == "Code"
    assert service.activate("workspace-a") is not None


def test_idle_protocol_adapter_creates_notification_and_forwards_events():
    registry = FakeRegistry()
    adapter = IdleProtocolAdapter()
    service = MagicMock()
    flush = MagicMock()
    adapter.set_flush_callback(flush)

    adapter.bind(registry=registry, name=10, version=99)
    adapter.bind_seat(registry=registry, name=11, version=99)
    adapter.start(service)

    notifier = registry.proxies["ext_idle_notifier_v1"]
    seat = registry.proxies["wl_seat"]
    assert len(notifier.notifications) == 1
    timeout, notification_seat, notification = notifier.notifications[0]
    assert timeout == 0
    assert notification_seat is seat
    flush.assert_called_once_with()

    notification.dispatcher["idled"](notification)
    notification.dispatcher["resumed"](notification)

    service.idled.assert_called_once_with()
    service.resumed.assert_called_once_with()


def test_wayland_idle_service_estimates_idle_seconds():
    now = 10.0

    def clock() -> float:
        return now

    protocol = MagicMock()
    service = WaylandIdleService(protocol=protocol, clock=clock)

    service.start()
    assert service.idle_seconds() is None

    service.idled()
    now = 15.5
    assert service.idle_seconds() == 5.5

    service.resumed()
    now = 18.0
    assert service.idle_seconds() == 2.5

    service.stop()
    protocol.start.assert_called_once_with(service)
    protocol.stop.assert_called_once_with()
    assert service.idle_seconds() is None


def test_workspace_adapter_flushes_activation_commit():
    adapter = WorkspaceProtocolAdapter()
    workspace = SimpleNamespace(activate=MagicMock())
    manager = SimpleNamespace(commit=MagicMock())
    flush = MagicMock()

    adapter._manager = manager
    adapter.set_flush_callback(flush)
    adapter.activate(workspace)

    workspace.activate.assert_called_once_with()
    manager.commit.assert_called_once_with()
    flush.assert_called_once_with()


def test_foreign_toplevel_adapter_replays_initial_events_after_service_start():
    adapter = ForeignToplevelProtocolAdapter()
    toplevel = FakeHandle()
    adapter._on_toplevel(None, toplevel)
    toplevel.dispatcher["title"](toplevel, "Terminal")
    toplevel.dispatcher["app_id"](toplevel, "foot")
    toplevel.dispatcher["state"](toplevel, ["activated"])
    toplevel.dispatcher["done"](toplevel)

    model = SimpleNamespace(
        visible_items=MagicMock(
            return_value=[SimpleNamespace(desktop_id="foot.desktop", wm_class="foot")]
        ),
        update_running=MagicMock(),
    )
    launcher = SimpleNamespace(resolve=MagicMock(), resolve_by_wm_class=MagicMock())
    service = WaylandForeignToplevelWindowService(
        model=model,
        launcher=launcher,
        protocol=adapter,
    )
    adapter.start(service)

    running = model.update_running.call_args.kwargs["running"]
    assert running["foot.desktop"].active is True
    assert service.list_windows("foot.desktop")[0].title == "Terminal"


def test_foreign_toplevel_adapter_requires_seat_for_activation():
    adapter = ForeignToplevelProtocolAdapter()
    handle = SimpleNamespace(activate=MagicMock())

    assert adapter.supports_action("activate", handle) is False
    adapter.activate(handle)
    handle.activate.assert_not_called()

    registry = FakeRegistry()
    adapter.bind_seat(registry=registry, name=12, version=99)

    assert adapter.supports_action("activate", handle) is True
    adapter.activate(handle)
    handle.activate.assert_called_once_with(registry.proxies["wl_seat"])


def test_foreign_toplevel_adapter_flushes_outgoing_actions():
    adapter = ForeignToplevelProtocolAdapter()
    handle = SimpleNamespace(
        activate=MagicMock(),
        close=MagicMock(),
        set_minimized=MagicMock(),
    )
    registry = FakeRegistry()
    flush = MagicMock()

    adapter.bind_seat(registry=registry, name=12, version=99)
    adapter.set_flush_callback(flush)
    adapter.activate(handle)
    adapter.set_minimized(handle)
    adapter.close(handle)

    handle.activate.assert_called_once_with(registry.proxies["wl_seat"])
    handle.set_minimized.assert_called_once_with()
    handle.close.assert_called_once_with()
    assert flush.call_count == 3


def test_preview_adapter_replays_initial_toplevels_after_tracker_start():
    adapter = PreviewProtocolAdapter()
    toplevel = FakeHandle()
    tracker = MagicMock()

    adapter._on_toplevel(None, toplevel)
    toplevel.dispatcher["title"](toplevel, "Files")
    toplevel.dispatcher["app_id"](toplevel, "org.gnome.Nautilus")
    toplevel.dispatcher["identifier"](toplevel, "opaque-handle-id")
    toplevel.dispatcher["done"](toplevel)
    adapter.start(tracker)

    tracker.toplevel_created.assert_called_once_with(toplevel)
    tracker.title_changed.assert_called_once_with(toplevel, "Files")
    tracker.app_id_changed.assert_called_once_with(toplevel, "org.gnome.Nautilus")
    tracker.identifier_changed.assert_called_once_with(toplevel, "opaque-handle-id")
    tracker.done.assert_called_once_with(toplevel)
