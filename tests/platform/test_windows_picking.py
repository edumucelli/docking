"""Native hit testing, process identity and handle ownership for Window Killer."""

import ctypes
import os
from dataclasses import replace
from unittest.mock import Mock

import pytest
from gi.repository import Gdk

from docking.platform.backends.base import (
    ActionResult,
    DisplayServer,
    WindowId,
    WindowSnapshot,
)
from docking.platform.backends.windows import picking


def native_api(monkeypatch, *, pid=101):
    api = picking.WindowsPickAPI.__new__(picking.WindowsPickAPI)
    window = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 100),
        desktop_id="",
        title="Picked application",
        pid=pid,
        wm_class="Example.Window",
        visible=True,
        minimized=False,
    )
    api._windows = Mock()
    api._windows.snapshot.return_value = window
    api._geometry = Mock()
    api._geometry.point.return_value = (-222, 333)
    api._flush = Mock(return_value=0)
    api._at_point = Mock(return_value=11)
    api._ancestor = Mock(return_value=100)
    api._exists = Mock(return_value=True)
    api._is_child = Mock(return_value=True)
    api._visible = Mock(return_value=True)
    owners = {100: pid, 11: pid, 999: 500, 55: 202, 56: 203}
    api._owner = Mock(
        side_effect=lambda h, output: setattr(output._obj, "value", owners.get(h, 0))
    )
    api._shell = Mock(return_value=999)
    api._open = Mock(return_value=0x100000001)
    api._close = Mock(return_value=1)
    api._wait = Mock(return_value=258)

    def times(handle, created, *_others):
        created._obj.low, created._obj.high = 123, 4
        return 1

    api._times = Mock(side_effect=times)
    api._critical = Mock(return_value=1)
    api._image = Mock(
        side_effect=lambda handle, flags, output, size: (
            setattr(output, "value", r"C:\Apps\Example.exe") or 1
        )
    )
    api._terminate = Mock(return_value=1)
    api._callback_type = lambda callback: callback
    api._children = Mock(side_effect=lambda root, callback, data: callback(55, 0))
    api._class = Mock(
        side_effect=lambda hwnd, output, size: setattr(
            output, "value", "Windows.UI.Core.CoreWindow"
        )
    )
    monkeypatch.setattr(Gdk.Display, "get_default", lambda: "display")
    return api, window, owners


def test_hit_testing_maps_logical_points_and_resolves_native_child_to_top_level(
    monkeypatch,
):
    api, window, _ = native_api(monkeypatch)
    target = api.pick(12, 34)
    assert target is not None and target.window == window
    assert target.created == (4 << 32) | 123
    assert ctypes.sizeof(picking._FileTime) == 8
    api._geometry.point.assert_called_once_with(12, 34, "display")
    point = api._at_point.call_args.args[0]
    assert (point.x, point.y) == (-222, 333)
    api._ancestor.assert_called_once_with(11, 2)
    api._open.assert_called_once_with(0x101000, False, 101)
    api._close.assert_called_once_with(0x100000001)


@pytest.mark.parametrize(
    "missing",
    ["display", "point", "composition", "hit", "root", "snapshot", "minimized"],
)
def test_unpickable_points_do_not_open_or_terminate_processes(monkeypatch, missing):
    api, window, _ = native_api(monkeypatch)
    if missing == "display":
        monkeypatch.setattr(Gdk.Display, "get_default", lambda: None)
    elif missing == "point":
        api._geometry.point.return_value = None
    elif missing == "composition":
        api._flush.return_value = -1
    elif missing == "hit":
        api._at_point.return_value = None
    elif missing == "root":
        api._ancestor.return_value = None
    elif missing == "snapshot":
        api._windows.snapshot.return_value = None
    else:
        api._windows.snapshot.return_value = replace(window, minimized=True)
    assert api.pick(1, 2) is None
    api._open.assert_not_called()
    api._terminate.assert_not_called()


@pytest.mark.parametrize(
    "blocked",
    ["self", "system", "shell", "critical", "critical-query", "image", "broker"],
)
def test_shell_critical_and_unidentified_processes_are_not_picked(monkeypatch, blocked):
    pid = os.getpid() if blocked == "self" else 4 if blocked == "system" else 101
    api, _, owners = native_api(monkeypatch, pid=pid)
    if blocked == "shell":
        owners[999] = pid
    elif blocked == "critical":
        api._critical.side_effect = lambda handle, output: (
            setattr(output._obj, "value", 1) or 1
        )
    elif blocked == "critical-query":
        api._critical.return_value = 0
    elif blocked == "image":
        api._image.side_effect = None
        api._image.return_value = 0
    elif blocked == "broker":
        api._image.side_effect = lambda handle, flags, output, size: (
            setattr(output, "value", r"C:\Windows\System32\ApplicationFrameHost.exe")
            or 1
        )
    assert api.pick(1, 2) is None
    api._close.assert_called_once_with(0x100000001)
    api._terminate.assert_not_called()


def test_hosted_application_resolves_its_visible_core_window_process(monkeypatch):
    api, window, _ = native_api(monkeypatch)
    api._windows.snapshot.return_value = replace(
        window, wm_class="ApplicationFrameWindow"
    )
    target = api.pick(1, 2)
    assert target is not None
    assert target.window.id == window.id and target.window.pid == 202
    assert target.root_pid == 101 and target.process_window == 55
    api._open.assert_called_once_with(0x101000, False, 202)
    assert api.valid(target)
    assert api.terminate(target) is ActionResult.OK
    assert all(c.args[2] == 202 for c in api._open.call_args_list)


@pytest.mark.parametrize(
    "association", ["live", "missing-export", "missing-window", "changed"]
)
def test_dwm_ghost_resolves_only_the_os_associated_original_application(
    monkeypatch, association
):
    api, window, owners = native_api(monkeypatch)
    owners[100], owners[200] = 500, 101  # Ghost broker and actual application.
    api._class.side_effect = lambda hwnd, output, size: setattr(
        output, "value", "Ghost"
    )
    api._windows.original_window.return_value = (
        None if association in ("missing-export", "missing-window") else 200
    )
    api._windows.snapshot.return_value = replace(
        window, id=WindowId(DisplayServer.WINDOWS, 200), visible=False
    )
    target = api.pick(1, 2)
    if association in ("missing-export", "missing-window"):
        assert target is None
        api._open.assert_not_called()
        return
    assert target is not None and target.window.pid == 101
    assert target.window.id.value == 200 and target.ghost_window == 100
    api._windows.snapshot.assert_called_once_with(200, include_hidden=True)
    if association == "changed":
        api._windows.original_window.return_value = 201
        assert not api.valid(target)
        assert api.terminate(target) is ActionResult.NOT_FOUND
        api._terminate.assert_not_called()
    else:
        assert api.valid(target)
        assert api.terminate(target) is ActionResult.OK
    assert all(c.args[2] == 101 for c in api._open.call_args_list)


@pytest.mark.parametrize("missing", ["hidden", "same-owner", "ambiguous", "callback"])
def test_missing_or_ambiguous_hosted_content_cannot_fall_back_to_killing_the_broker(
    monkeypatch, missing
):
    api, window, owners = native_api(monkeypatch)
    api._windows.snapshot.return_value = replace(
        window, wm_class="ApplicationFrameWindow"
    )
    if missing == "hidden":
        api._visible.return_value = False
    elif missing == "same-owner":
        owners[55] = 101
    elif missing == "ambiguous":
        api._children.side_effect = lambda root, callback, data: (
            callback(55, 0),
            callback(56, 0),
        )
    else:

        def child_disappeared(hwnd, output, size):
            if hwnd == 55:
                raise OSError("Window vanished")
            output.value = "ApplicationFrameWindow"

        api._class.side_effect = child_disappeared
    assert api.pick(1, 2) is None
    api._open.assert_not_called()


@pytest.mark.parametrize("failure", ["open", "exited", "times", "exception"])
def test_process_query_failure_releases_each_opened_handle(monkeypatch, failure):
    api, _, _ = native_api(monkeypatch)
    if failure == "open":
        api._open.return_value = None
    elif failure == "exited":
        api._wait.return_value = 0
    elif failure == "times":
        api._times.side_effect = None
        api._times.return_value = 0
    else:
        api._times.side_effect = OSError("Process vanished")
    if failure == "exception":
        with pytest.raises(OSError):
            api.pick(1, 2)
    else:
        assert api.pick(1, 2) is None
    assert api._close.call_count == (0 if failure == "open" else 1)


@pytest.mark.parametrize(
    "change", ["root-pid", "content-pid", "detached", "creation", "gone"]
)
def test_stale_host_window_or_reused_process_cannot_redirect_termination(
    monkeypatch, change
):
    api, window, owners = native_api(monkeypatch)
    api._windows.snapshot.return_value = replace(
        window, wm_class="ApplicationFrameWindow"
    )
    target = api.pick(1, 2)
    assert target is not None
    if change == "root-pid":
        owners[100] = 303
    elif change == "content-pid":
        owners[55] = 303
    elif change == "detached":
        api._is_child.return_value = False
    elif change == "creation":
        api._times.side_effect = lambda *_args: 1
    else:
        api._exists.return_value = False
    assert not api.valid(target)
    assert api.terminate(target) is ActionResult.NOT_FOUND
    api._terminate.assert_not_called()


def test_termination_revalidates_after_open_and_keeps_the_exact_process_handle(
    monkeypatch,
):
    api, _, owners = native_api(monkeypatch)
    target = api.pick(1, 2)
    assert target is not None
    api._close.reset_mock()
    assert api.terminate(target) is ActionResult.OK
    assert api._open.call_args.args == (0x101001, False, 101)
    api._terminate.assert_called_once_with(0x100000001, 1)
    api._close.assert_called_once_with(0x100000001)
    api._terminate.reset_mock()

    def changed_after_open(*_args):
        owners[100] = 303
        return 0x100000002

    api._open.side_effect = changed_after_open
    assert api.terminate(target) is ActionResult.NOT_FOUND
    api._terminate.assert_not_called()
    assert api._close.call_args.args == (0x100000002,)


@pytest.mark.parametrize("failure", ["denied", "terminate", "exception", "critical"])
def test_termination_failure_is_reported_without_leaking_a_handle(monkeypatch, failure):
    api, _, _ = native_api(monkeypatch)
    target = api.pick(1, 2)
    assert target is not None
    api._close.reset_mock()
    if failure == "denied":
        api._open.return_value = None
    elif failure == "terminate":
        api._terminate.return_value = 0
    elif failure == "exception":
        api._terminate.side_effect = OSError("Terminating failed")
    else:
        api._critical.side_effect = lambda handle, output: (
            setattr(output._obj, "value", 1) or 1
        )
    if failure == "exception":
        with pytest.raises(OSError):
            api.terminate(target)
    else:
        expected = (
            ActionResult.UNSUPPORTED if failure == "critical" else ActionResult.FAILED
        )
        assert api.terminate(target) is expected
    assert api._close.call_count == (0 if failure == "denied" else 1)


def test_service_selection_is_single_use_and_validates_pid_before_reporting_it():
    window = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 1), desktop_id="", pid=101
    )
    target = picking.PickedWindow(window, 101, 1, 123)
    api = Mock()
    api.pick.return_value = target
    api.valid.return_value = True
    api.terminate.return_value = ActionResult.OK
    service = picking.WindowsWindowPickService(api=api)
    assert service.pick_window_at(x=2, y=3) == window
    assert service.pid_for(window.id) == 101
    assert service.pid_for(WindowId.x11(1)) is None
    assert service.kill(window.id) is ActionResult.OK
    assert service.kill(window.id) is ActionResult.NOT_FOUND
    assert service.pid_for(window.id) is None
    api.terminate.assert_called_once_with(target)
    api.valid.assert_called_once_with(target)


def test_service_rejects_old_selections_after_failure_stop_or_restart():
    window = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 1), desktop_id="", pid=101
    )
    api = Mock()
    api.pick.return_value = picking.PickedWindow(window, 101, 1, 123)
    service = picking.WindowsWindowPickService(api=api)
    assert service.pick_window_at(x=0, y=0)
    service.stop()
    assert service.pick_window_at(x=0, y=0) is None
    assert service.kill(window.id) is ActionResult.NOT_FOUND
    service.start()
    assert service.kill(window.id) is ActionResult.NOT_FOUND
    assert service.pick_window_at(x=0, y=0)
    api.pick.side_effect = OSError("Unavailable")
    assert service.pick_window_at(x=0, y=0) is None
    assert service.kill(window.id) is ActionResult.NOT_FOUND
    api.terminate.assert_not_called()


@pytest.mark.parametrize("during", ["pick", "query"])
def test_generation_changes_cannot_publish_an_old_selection_or_pid(during):
    window = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 1), desktop_id="", pid=101
    )
    api = Mock()
    target = picking.PickedWindow(window, 101, 1, 123)
    api.pick.return_value = target
    service = picking.WindowsWindowPickService(api=api)

    def restart():
        service.stop()
        service.start()
        return target if during == "pick" else True

    if during == "pick":
        api.pick.side_effect = lambda *_args: restart()
        assert service.pick_window_at(x=0, y=0) is None
    else:
        assert service.pick_window_at(x=0, y=0)
        api.valid.side_effect = lambda *_args: restart()
        assert service.pid_for(window.id) is None
    assert service.kill(window.id) is ActionResult.NOT_FOUND
    api.terminate.assert_not_called()


def test_native_libraries_are_lazy_and_native_failures_have_common_results(monkeypatch):
    dll = Mock(side_effect=OSError("Unavailable"))
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    service = picking.WindowsWindowPickService()
    service.start()
    service.stop()
    assert service.pick_window_at(x=0, y=0) is None
    dll.assert_not_called()
    service.start()
    assert service.pick_window_at(x=0, y=0) is None
    dll.assert_called_once()


def test_native_bindings_keep_process_handles_wide_and_pids_and_filetimes_fixed_width(
    monkeypatch,
):
    user, kernel, dwm = Mock(), Mock(), Mock()
    libraries = {"user32": user, "kernel32": kernel, "dwmapi": dwm}
    monkeypatch.setattr(
        ctypes, "WinDLL", lambda name, **_kw: libraries[name], raising=False
    )
    monkeypatch.setattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE, raising=False)
    monkeypatch.setattr(picking, "Win32WindowAPI", Mock())
    monkeypatch.setattr(picking, "WindowsMonitorGeometry", Mock())
    picking.WindowsPickAPI()
    assert kernel.OpenProcess.restype is ctypes.c_void_p
    assert kernel.OpenProcess.argtypes == (
        ctypes.c_uint32,
        ctypes.c_int32,
        ctypes.c_uint32,
    )
    assert kernel.TerminateProcess.argtypes == (ctypes.c_void_p, ctypes.c_uint32)
    assert kernel.CloseHandle.argtypes == (ctypes.c_void_p,)
    assert kernel.GetProcessTimes.argtypes == (
        ctypes.c_void_p,
        *([ctypes.POINTER(picking._FileTime)] * 4),
    )
    assert user.WindowFromPoint.restype is ctypes.c_void_p


def test_service_native_query_and_kill_errors_expire_selection_without_raising():
    window = WindowSnapshot(
        id=WindowId(DisplayServer.WINDOWS, 1), desktop_id="", pid=101
    )
    api = Mock()
    api.pick.return_value = picking.PickedWindow(window, 101, 1, 123)
    api.valid.side_effect = OSError("Cannot query process")
    api.terminate.side_effect = OSError("Termination denied")
    service = picking.WindowsWindowPickService(api=api)
    assert service.pick_window_at(x=0, y=0)
    assert service.pid_for(window.id) is None
    assert service.kill(window.id) is ActionResult.FAILED
    assert service.kill(window.id) is ActionResult.NOT_FOUND
    assert service._selected is None
