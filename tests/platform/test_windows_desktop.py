"""Shell desktop ordering, idempotent actions and COM resource ownership."""

import ctypes
import threading
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from docking.platform.backends.base import ActionResult
from docking.platform.backends.windows import desktop


def state_api(*, below=(10, 20)):
    api = desktop.WindowsDesktopAPI.__new__(desktop.WindowsDesktopAPI)
    api._shell = Mock(return_value=1)
    api._pid = Mock(return_value=55)
    order = [1, *below, 0]
    api._next = Mock(side_effect=lambda hwnd, _: order[order.index(hwnd) + 1])
    api._visible = Mock(return_value=1)
    api._style = Mock(return_value=0)
    api._class = Mock(
        side_effect=lambda hwnd, output, size: setattr(output, "value", "App")
    )
    api._cloaked = Mock(return_value=0)
    return api


def test_raised_desktop_covers_mapped_windows_without_assuming_they_are_minimized():
    assert state_api().covered_windows() == frozenset({10, 20})
    assert state_api(below=()).covered_windows() == frozenset()


def test_cloaked_topmost_and_other_shell_surfaces_are_excluded():
    api = state_api(below=(10, 20, 30, 40, 50))
    api._visible.side_effect = lambda h: h != 10
    api._style.side_effect = lambda h, _: 8 if h == 20 else 0
    api._class.side_effect = lambda h, output, _: setattr(
        output, "value", "WorkerW" if h == 30 else "App"
    )
    api._cloaked.side_effect = lambda h, key, output, size: (
        setattr(output._obj, "value", int(h == 40)) or 0
    )
    assert api.covered_windows() == frozenset({10, 50})


@pytest.mark.parametrize(
    "unknown", ["absent", "hidden", "owner", "cycle", "replacement"]
)
def test_incomplete_or_restarted_shell_does_not_establish_desktop_state(unknown):
    api = state_api()
    if unknown == "absent":
        api._shell.return_value = None
    elif unknown == "hidden":
        api._visible.return_value = 0
    elif unknown == "owner":
        api._pid.return_value = 0
    elif unknown == "cycle":
        api._next.return_value = 10
        api._next.side_effect = None
    else:
        api._shell.side_effect = [1, 2]
    assert api.covered_windows() is None


def complete(service):
    with service._condition:
        assert service._condition.wait_for(lambda: not service.pending, timeout=2)
    return service.last_result


def switching_api(showing=False, *, delay=0):
    api = Mock()
    state = [showing, None, 0]

    def observe():
        if state[1] is not None:
            if state[2] <= 0:
                state[0], state[1] = state[1], None
            else:
                state[2] -= 1
        return frozenset({10}) if state[0] else frozenset()

    def toggle():
        state[1] = not state[0]
        state[2] = delay
        return True

    api.covered_windows.side_effect = observe
    api.toggle.side_effect = toggle
    return api, state


@pytest.mark.parametrize(
    "showing,wanted,invokes",
    [
        (False, True, 1),
        (True, True, 0),
        (False, False, 0),
        (True, False, 1),
        (False, None, 1),
        (True, None, 1),
    ],
)
def test_explicit_actions_observe_real_shell_order_and_toggle_only_if_required(
    showing, wanted, invokes
):
    api, _ = switching_api(showing)
    service = desktop.WindowsDesktopActionService(api=api)
    try:
        assert service.show_desktop(wanted) is ActionResult.OK
        assert complete(service) is ActionResult.OK
        assert api.toggle.call_count == invokes
    finally:
        service.stop()


def test_rapid_duplicate_explicit_requests_wait_for_asynchronous_shell_transitions():
    api, state = switching_api(delay=3)
    service = desktop.WindowsDesktopActionService(api=api)
    try:
        for show in (True, True, False, False, None, None):
            assert service.show_desktop(show) is ActionResult.OK
        assert complete(service) is ActionResult.OK
        assert state[0] is False
        assert api.toggle.call_count == 4
    finally:
        service.stop()


def test_unknown_state_failure_and_stopped_lifetime_reject_actions(monkeypatch):
    monkeypatch.setattr(desktop, "_SHELL_READY_SECONDS", 0.01)
    api = Mock()
    api.covered_windows.return_value = None
    service = desktop.WindowsDesktopActionService(api=api)
    try:
        assert service.show_desktop(True) is ActionResult.OK
        assert complete(service) is ActionResult.NOT_FOUND
        api.toggle.assert_not_called()
        api.covered_windows.return_value = frozenset()
        api.toggle.side_effect = OSError("unavailable")
        assert service.show_desktop() is ActionResult.OK
        assert complete(service) is ActionResult.FAILED
        service.stop()
        assert service.show_desktop() is ActionResult.NOT_FOUND
        assert api.toggle.call_count == 1
        service.start()
        assert service.last_result is None
        assert service.show_desktop(False) is ActionResult.OK
        assert complete(service) is ActionResult.OK
    finally:
        service.stop()


def test_shell_recovery_waits_for_desktop_readiness_before_issuing_the_action():
    api, _ = switching_api()
    observe = api.covered_windows.side_effect
    reads = [None, None, None]
    api.covered_windows.side_effect = lambda: reads.pop() if reads else observe()
    service = desktop.WindowsDesktopActionService(api=api)
    try:
        assert service.show_desktop(True) is ActionResult.OK
        assert complete(service) is ActionResult.OK
        api.toggle.assert_called_once()
        assert not reads
    finally:
        service.stop()


def test_stop_interrupts_shell_recovery_without_dispatching_an_action():
    api = Mock()
    observed = threading.Event()
    api.covered_windows.side_effect = lambda: observed.set() or None
    service = desktop.WindowsDesktopActionService(api=api)
    assert service.show_desktop(True) is ActionResult.OK
    assert observed.wait(1)
    service.stop()
    assert not service.pending and service._thread is None
    api.toggle.assert_not_called()


def test_new_shell_window_waits_for_registration_before_dispatching():
    api, _ = switching_api()
    toggle = api.toggle.side_effect
    readiness = [False, False, False]
    api.toggle.side_effect = lambda: readiness.pop() if readiness else toggle()
    service = desktop.WindowsDesktopActionService(api=api)
    try:
        assert service.show_desktop(True) is ActionResult.OK
        assert complete(service) is ActionResult.OK
        assert api.toggle.call_count == 4
    finally:
        service.stop()


def test_external_change_during_shell_registration_does_not_issue_a_second_toggle():
    api, state = switching_api()

    def pending():
        state[0] = True
        return False

    api.toggle.side_effect = pending
    service = desktop.WindowsDesktopActionService(api=api)
    try:
        assert service.show_desktop() is ActionResult.OK
        assert complete(service) is ActionResult.OK
        api.toggle.assert_called_once()
    finally:
        service.stop()


def test_native_libraries_are_lazy_and_stopped_queries_do_not_load_them(monkeypatch):
    dll = Mock(side_effect=OSError("unavailable"))
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    service = desktop.WindowsDesktopActionService()
    service.start()
    service.stop()
    assert service.show_desktop() is ActionResult.NOT_FOUND
    dll.assert_not_called()
    service.start()
    assert service.show_desktop() is ActionResult.FAILED
    assert not service.pending


def test_stop_and_restart_during_observation_cannot_issue_an_old_action():
    api = Mock()
    service = desktop.WindowsDesktopActionService(api=api)

    def observe():
        service.stop()
        service.start()
        return frozenset()

    api.covered_windows.side_effect = observe
    try:
        assert service.show_desktop(True) is ActionResult.OK
        assert complete(service) is None
        api.toggle.assert_not_called()
    finally:
        service.stop()


def test_stopping_cancels_queued_requests_and_interrupts_transition_wait():
    api = Mock()
    invoked = threading.Event()
    api.covered_windows.return_value = frozenset()
    api.toggle.side_effect = invoked.set
    service = desktop.WindowsDesktopActionService(api=api)
    assert service.show_desktop(True) is ActionResult.OK
    assert invoked.wait(1)
    for _ in range(64):
        assert service.show_desktop(True) is ActionResult.OK
    assert service.show_desktop(True) is ActionResult.FAILED
    service.stop()
    assert not service.pending and service._thread is None
    api.toggle.assert_called_once()


def test_unexpected_request_failure_is_observable_and_does_not_lose_later_work():
    api, _ = switching_api()
    observe = api.covered_windows.side_effect
    api.covered_windows.side_effect = [ValueError("bad native response")]
    service = desktop.WindowsDesktopActionService(api=api)
    try:
        assert service.show_desktop(True) is ActionResult.OK
        assert complete(service) is ActionResult.FAILED
        api.covered_windows.side_effect = observe
        assert service.show_desktop(True) is ActionResult.OK
        assert complete(service) is ActionResult.OK
    finally:
        service.stop()


@pytest.mark.parametrize(
    "failure", [None, "activation", "invoke", "restart", "pending"]
)
def test_com_toggle_uses_sdk_slot_and_always_releases_the_interface(
    monkeypatch, failure
):
    api = state_api()
    api._allow = Mock()
    api._registered = Mock(return_value=failure != "pending")
    ole = Mock()
    lifetimes = []

    @contextmanager
    def apartment():
        lifetimes.append("enter")
        try:
            yield ole
        finally:
            lifetimes.append("exit")

    def create(clsid, outer, context, iid, output):
        assert context == 5
        assert bytes(clsid._obj) == bytes(desktop._SHELL_CLASS)
        assert bytes(iid._obj) == bytes(desktop._SHELL_INTERFACE)
        output._obj.value = 123
        return -1 if failure == "activation" else 0

    ole.CoCreateInstance.side_effect = create
    invoke = Mock(return_value=-1 if failure == "invoke" else 0)
    method, release = Mock(return_value=invoke), Mock()
    monkeypatch.setattr(desktop, "apartment", apartment)
    monkeypatch.setattr(desktop, "method", method)
    monkeypatch.setattr(desktop, "release", release)
    if failure == "restart":
        api._shell.side_effect = [1, 2]
    if failure and failure != "pending":
        with pytest.raises(OSError):
            api.toggle()
    else:
        assert api.toggle() is (failure != "pending")
    assert lifetimes == ["enter", "exit"]
    if failure == "pending":
        ole.CoCreateInstance.assert_not_called()
        release.assert_not_called()
    else:
        release.assert_called_once()
    if failure not in ("activation", "restart", "pending"):
        assert method.call_args.args[1:] == (41, desktop.HRESULT)


@pytest.mark.parametrize(
    "failure", [None, "activation", "find", "pending", "empty", "replacement"]
)
def test_shell_registration_queries_current_desktop_and_releases_both_interfaces(
    monkeypatch, failure
):
    api = state_api()
    ole = Mock()

    def create(clsid, outer, context, iid, output):
        assert context == 4
        assert bytes(clsid._obj) == bytes(desktop._WINDOWS_CLASS)
        assert bytes(iid._obj) == bytes(desktop._WINDOWS_INTERFACE)
        output._obj.value = 123
        return -1 if failure == "activation" else 0

    def find(pointer, location, empty, classes, window, options, output):
        assert location._obj.vt == 3 and location._obj.data.integer == 0
        assert empty._obj.vt == 0
        assert classes == 8 and options == 1
        assert ctypes.sizeof(location._obj) == (
            24 if ctypes.sizeof(ctypes.c_void_p) == 8 else 16
        )
        assert ctypes.sizeof(window._obj) == 4
        window._obj.value = 2 if failure == "replacement" else 1
        output._obj.value = None if failure == "empty" else 456
        return -1 if failure == "find" else int(failure == "pending")

    ole.CoCreateInstance.side_effect = create
    method, release = Mock(return_value=find), Mock()
    monkeypatch.setattr(desktop, "method", method)
    monkeypatch.setattr(desktop, "release", release)
    if failure in ("activation", "find"):
        with pytest.raises(OSError):
            api._registered(ole, 1)
    else:
        assert api._registered(ole, 1) is (failure is None)
    assert release.call_count == 2
    assert release.call_args_list[-1].args[0].value == 123
    if failure != "activation":
        assert method.call_args.args[1:3] == (15, desktop.HRESULT)
