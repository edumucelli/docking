"""Worker isolation, desktop transitions and stale workspace membership."""

import threading
import time
from types import MappingProxyType, SimpleNamespace
from unittest.mock import Mock

import pytest
from gi.repository import GLib

from docking.platform.backends.base import ActionResult, WorkspaceSnapshot
from docking.platform.backends.windows import workspaces
from docking.platform.backends.windows.virtual_desktops import (
    VirtualDesktopState,
    WindowWorkspace,
)
from docking.platform.backends.windows.workspaces import WindowsWorkspaceService

FIRST = "00000000-0000-0000-0000-000000000001"
SECOND = "00000000-0000-0000-0000-000000000002"
_CONTEXT = GLib.MainContext.new()


@pytest.fixture(autouse=True)
def isolated_main_context(monkeypatch):
    # Exercise actual GLib delivery on the test thread without dispatching
    # unrelated GTK callbacks retained by the rest of the full suite.
    def idle_add(callback):
        source = GLib.idle_source_new()
        source.set_callback(lambda *_args: callback())
        return source.attach(_CONTEXT)

    def source_remove(identifier):
        source = _CONTEXT.find_source_by_id(identifier)
        if source is not None:
            source.destroy()

    monkeypatch.setattr(
        workspaces,
        "GLib",
        SimpleNamespace(idle_add=idle_add, source_remove=source_remove),
    )


def snapshot(active=FIRST, *, name="Second"):
    return VirtualDesktopState(
        (
            WorkspaceSnapshot(FIRST, 0, "First", active == FIRST),
            WorkspaceSnapshot(SECOND, 1, name, active == SECOND),
        ),
        MappingProxyType({100: WindowWorkspace(55, FIRST, active == FIRST)}),
    )


def wait_for(predicate):
    deadline = time.monotonic() + 4
    context = _CONTEXT
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


class DesktopAPI:
    def __init__(self):
        self.state = snapshot()
        self.calls = []
        self.threads = []

    def supported(self):
        return True

    def read(self):
        self.threads.append(threading.get_ident())
        return self.state

    def activate(self, identifier, cancelled):
        if cancelled():
            return ActionResult.NOT_FOUND
        self.calls.append(identifier)
        self.state = snapshot(identifier)
        return ActionResult.OK


@pytest.fixture
def running():
    api = DesktopAPI()
    service = WindowsWorkspaceService(api=api)
    service.start()
    wait_for(lambda: service.available)
    try:
        yield service, api
    finally:
        service.stop()


def test_calls_run_off_gtk_and_only_main_loop_publishes_snapshots():
    api = DesktopAPI()
    read = threading.Event()
    original = api.read
    api.read = lambda: (read.set(), original())[1]
    service = WindowsWorkspaceService(api=api)
    callbacks = []
    service.watch_active_workspace(lambda: callbacks.append(threading.get_ident()))
    service.start()
    try:
        assert read.wait(1)
        assert not service.available and not service.list_workspaces()
        wait_for(lambda: service.available)
        assert callbacks == [threading.get_ident()]
        assert all(t != threading.get_ident() for t in api.threads)
        assert service.active_workspace().id == FIRST
        assert service.membership(100, 55).desktop_id == FIRST
        assert service.membership(100, 56) is None
        assert service.membership(101, 55) is None
    finally:
        service.stop()
    assert not service.list_workspaces() and not service.available
    assert service.membership(100, 55) is None
    assert not service._sources and not service._watchers


def test_switch_requests_follow_order_and_focus_only_after_native_confirmation(running):
    service, api = running
    focused = []
    for wanted in (SECOND, FIRST, SECOND):
        assert (
            service.activate_then(
                wanted,
                lambda value=wanted: (
                    focused.append(api.state.desktops[1].active) or ActionResult.OK
                ),
            )
            is ActionResult.OK
        )
    wait_for(lambda: not service.pending and service.active_workspace().id == SECOND)
    assert api.calls == [SECOND, FIRST, SECOND]
    assert focused == [True, False, True]
    assert service.last_result is ActionResult.OK


def test_external_name_list_and_membership_changes_notify_without_switching(running):
    service, api = running
    changes = []
    handle = service.watch_active_workspace(
        lambda: changes.append(service.list_workspaces())
    )
    api.state = snapshot(name="Renamed")
    wait_for(lambda: service.list_workspaces()[1].name == "Renamed")
    assert len(changes) == 1 and not api.calls
    api.state = VirtualDesktopState((api.state.desktops[0],), MappingProxyType({}))
    wait_for(lambda: len(service.list_workspaces()) == 1)
    assert len(changes) == 2 and service.membership(100, 55) is None
    service.unwatch_active_workspace(handle)
    api.state = snapshot(SECOND)
    wait_for(lambda: service.active_workspace().id == SECOND)
    assert len(changes) == 2 and not api.calls


def test_invalid_missing_and_stopped_requests_do_not_touch_shell(running):
    service, api = running
    for value in ("invalid", "00000000-0000-0000-0000-000000000003"):
        assert service.activate(value) is ActionResult.NOT_FOUND
    service.stop()
    assert service.activate(FIRST) is ActionResult.NOT_FOUND
    assert not api.calls


def test_unsupported_build_never_loads_native_libraries():
    api = Mock()
    api.supported.return_value = False
    service = WindowsWorkspaceService(api=api)
    service.start()
    try:
        assert service.activate(FIRST) is ActionResult.UNSUPPORTED
        assert service._thread is None
        api.read.assert_not_called()
        api.activate.assert_not_called()
    finally:
        service.stop()


def test_shell_failure_discards_membership_then_recovers(running):
    service, api = running
    original = api.read
    api.read = Mock(side_effect=OSError("Explorer restarting"))
    wait_for(lambda: not service.available)
    assert not service.list_workspaces() and service.membership(100, 55) is None
    assert service.activate(SECOND) is ActionResult.UNSUPPORTED
    api.read = original
    wait_for(lambda: service.available)
    assert service.active_workspace().id == FIRST


def test_stop_restart_drops_inflight_snapshot_queued_switch_and_deferred_focus(running):
    service, api = running
    entered, release = threading.Event(), threading.Event()
    focus = Mock(return_value=ActionResult.OK)
    original = api.activate

    def blocked(identifier, cancelled):
        entered.set()
        assert release.wait(3)
        return original(identifier, cancelled)

    api.activate = blocked
    assert service.activate_then(SECOND, focus) is ActionResult.OK
    assert entered.wait(1)
    assert service.activate(FIRST) is ActionResult.OK
    service.stop()
    service.start()
    release.set()
    wait_for(lambda: service.available and not service.pending)
    assert service.active_workspace().id == FIRST
    assert not api.calls
    focus.assert_not_called()
    assert service.last_result is None
    assert service.activate(SECOND) is ActionResult.OK
    wait_for(lambda: not service.pending and service.active_workspace().id == SECOND)
    assert api.calls == [SECOND]


@pytest.mark.parametrize("result", [ActionResult.NOT_FOUND, ActionResult.FAILED])
def test_failed_switch_does_not_focus_an_unrelated_desktop(running, result):
    service, api = running
    api.activate = Mock(return_value=result)
    focus = Mock(return_value=ActionResult.OK)
    assert service.activate_then(SECOND, focus) is ActionResult.OK
    wait_for(lambda: not service.pending)
    assert service.last_result is result
    assert service.active_workspace().id == FIRST
    focus.assert_not_called()


def test_queue_is_bounded_and_stop_cancels_wait_for_native_transition(running):
    service, api = running
    entered = threading.Event()
    api.activate = lambda *_args: entered.set() or ActionResult.OK
    assert service.activate(SECOND) is ActionResult.OK
    assert entered.wait(1)
    for _ in range(64):
        assert service.activate(SECOND) is ActionResult.OK
    assert service.activate(SECOND) is ActionResult.FAILED
    service.stop()
    assert not service.pending and service._thread is None
    assert not api.calls


def test_focus_failure_is_reported_after_successful_desktop_switch(running):
    service, _api = running
    assert (
        service.activate_then(SECOND, lambda: ActionResult.NOT_FOUND) is ActionResult.OK
    )
    wait_for(lambda: not service.pending and service.active_workspace().id == SECOND)
    assert service.last_result is ActionResult.NOT_FOUND


def test_surface_scope_runs_on_worker_and_retries_new_shell_view_without_losing_desktops(
    running,
):
    service, api = running
    calls = []

    def pin(hwnd, pinned):
        calls.append((hwnd, pinned, threading.get_ident()))
        if len(calls) == 1:
            raise OSError("Shell view not registered yet")

    api.set_pinned = pin
    service.set_surface_scope(123, current_workspace_only=False)
    wait_for(lambda: len(calls) >= 2)
    assert service.available and service.active_workspace().id == FIRST
    assert all(c[:2] == (123, True) and c[2] != threading.get_ident() for c in calls)
    service.set_surface_scope(123, current_workspace_only=True)
    wait_for(lambda: any(c[1] is False for c in calls))
    service.stop()
    assert service._scope is None


@pytest.mark.parametrize("supported", [False, True])
def test_windows_composition_owns_common_workspace_contract_and_gates_capabilities(
    monkeypatch, supported
):
    from docking.platform.backends.base import WorkspaceService
    from docking.platform.backends.windows import session, workspaces

    api = DesktopAPI()
    api.supported = lambda: supported
    service = WindowsWorkspaceService(api=api)
    monkeypatch.setattr(workspaces, "WindowsWorkspaceService", lambda: service)
    backend = session.WindowsSessionBackend(
        model=Mock(),
        config=Mock(),
        application_registry=Mock(),
        process_identity_service=Mock(),
    )
    assert backend.workspaces is service and isinstance(service, WorkspaceService)
    capabilities = backend.capabilities
    assert capabilities.supports_workspace_list is supported
    assert capabilities.supports_workspace_switch is supported
    assert capabilities.supports_current_workspace_filter is supported
    assert capabilities.tracks_window_workspace is supported
    assert backend.windows._workspaces is service
    assert backend.surface._workspaces is service
