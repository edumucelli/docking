"""Native session ABI, privilege scope, availability and request ownership."""

import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.session import backend, windows


@pytest.fixture
def api(monkeypatch):
    last = [0]
    monkeypatch.setattr(ctypes, "get_last_error", lambda: last[0], raising=False)
    monkeypatch.setattr(
        ctypes, "set_last_error", lambda code: last.__setitem__(0, code), raising=False
    )
    names = (
        "LockWorkStation",
        "ExitWindowsEx",
        "SetSuspendState",
        "GetPwrCapabilities",
        "GetCurrentProcess",
        "CloseHandle",
        "OpenProcessToken",
        "LookupPrivilegeValueW",
        "GetTokenInformation",
        "AdjustTokenPrivileges",
    )
    dll = SimpleNamespace(**{n: Mock(return_value=1) for n in names})
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_a, **_kw: dll, raising=False)

    def open_token(_process, _access, handle):
        ctypes.cast(handle, ctypes.POINTER(ctypes.c_void_p))[0] = 1234
        return 1

    def lookup(_system, name, luid):
        assert name == "SeShutdownPrivilege"
        ctypes.cast(luid, ctypes.POINTER(windows._Luid)).contents.low = 19
        return 1

    def info(_token, kind, buffer, capacity, size):
        assert kind == 3
        value = windows._TokenPrivileges(count=1)
        value.privileges[0] = windows._Privilege(windows._Luid(19, 0), 0)
        ctypes.cast(size, ctypes.POINTER(windows._DWORD))[0] = ctypes.sizeof(value)
        if capacity < ctypes.sizeof(value):
            last[0] = 122
            return 0
        ctypes.memmove(buffer, ctypes.byref(value), ctypes.sizeof(value))
        return 1

    def caps(pointer):
        value = ctypes.cast(
            pointer, ctypes.POINTER(windows._PowerCapabilities)
        ).contents
        value.s3, value.s5 = 1, 1
        return 1

    def adjust(_token, disable_all, new, _capacity, old, _size):
        assert not disable_all
        value = ctypes.cast(new, ctypes.POINTER(windows._TokenPrivileges)).contents
        assert value.count == 1 and value.privileges[0].luid.low == 19
        if old:
            previous = ctypes.cast(
                old, ctypes.POINTER(windows._TokenPrivileges)
            ).contents
            previous.count = 1
            previous.privileges[0] = windows._Privilege(windows._Luid(19, 0), 0)
        return 1

    dll.OpenProcessToken.side_effect = open_token
    dll.LookupPrivilegeValueW.side_effect = lookup
    dll.GetTokenInformation.side_effect = info
    dll.GetPwrCapabilities.side_effect = caps
    dll.AdjustTokenPrivileges.side_effect = adjust
    return windows._PowerAPI(), dll, last


def test_native_session_structures_match_windows_sdk():
    assert ctypes.sizeof(windows._Luid) == 8
    assert ctypes.sizeof(windows._Privilege) == 12
    assert windows._TokenPrivileges.privileges.offset == 4
    assert ctypes.sizeof(windows._TokenPrivileges) == 16
    assert ctypes.sizeof(windows._PowerCapabilities) == 76
    assert windows._PowerCapabilities.aoac.offset == 20
    assert windows._PowerCapabilities.battery_scale.offset == 32


def test_factory_selects_windows_before_unix(monkeypatch):
    monkeypatch.setattr(backend.environment, "is_windows", lambda: True)
    monkeypatch.setattr(backend.environment, "is_bsd", lambda: True)
    assert isinstance(backend.create_backend(), windows.WindowsSessionBackend)


def test_capabilities_require_existing_right_and_actual_sleep_support(api, monkeypatch):
    native, dll, _last = api
    assert native.available_actions() == {
        "lock",
        "logout",
        "suspend",
        "reboot",
        "poweroff",
    }
    dll.AdjustTokenPrivileges.assert_not_called()
    dll.CloseHandle.assert_called_once()
    monkeypatch.setattr(native, "_can_shutdown", lambda: False)
    assert native.available_actions() == {"lock", "logout"}


@pytest.mark.parametrize("field", [None, "s1", "s2", "s3", "aoac"])
def test_suspend_is_disabled_without_a_sleep_state_including_modern_standby(api, field):
    native, dll, _last = api

    def caps(pointer):
        value = ctypes.cast(
            pointer, ctypes.POINTER(windows._PowerCapabilities)
        ).contents
        if field:
            setattr(value, field, 1)
        return 1

    dll.GetPwrCapabilities.side_effect = caps
    actions = native.available_actions()
    assert ("suspend" in actions) == (field is not None)
    assert "poweroff" not in actions


def test_token_privilege_count_is_bounded_and_handle_always_closed(api):
    native, dll, _last = api
    original = dll.GetTokenInformation.side_effect

    def malformed(token, kind, buffer, capacity, size):
        result = original(token, kind, buffer, capacity, size)
        if result:
            ctypes.cast(buffer, ctypes.POINTER(windows._DWORD))[0] = 1000
        return result

    dll.GetTokenInformation.side_effect = malformed
    with pytest.raises(OSError, match="exceeds"):
        native._can_shutdown()
    dll.CloseHandle.assert_called_once()


@pytest.mark.parametrize(
    "action,flags", [("logout", 0), ("reboot", 2), ("poweroff", 8)]
)
def test_exit_is_graceful_and_shutdown_privilege_is_restored(api, action, flags):
    native, dll, _last = api
    restored = []
    original = dll.AdjustTokenPrivileges.side_effect

    def adjust(token, disable, new, capacity, old, size):
        value = ctypes.cast(new, ctypes.POINTER(windows._TokenPrivileges)).contents
        restored.append(value.privileges[0].attributes)
        return original(token, disable, new, capacity, old, size)

    dll.AdjustTokenPrivileges.side_effect = adjust
    native.perform(action)
    dll.ExitWindowsEx.assert_called_once_with(flags, 0x80040000)
    assert restored == ([] if action == "logout" else [2, 0])
    assert dll.CloseHandle.call_count == (0 if action == "logout" else 1)


def test_suspend_uses_boolean_abi_and_keeps_wake_events_enabled(api):
    native, dll, _last = api
    native.perform("suspend")
    dll.SetSuspendState.assert_called_once_with(False, False, False)
    assert dll.SetSuspendState.restype is ctypes.c_ubyte
    assert dll.AdjustTokenPrivileges.call_count == 2
    dll.ExitWindowsEx.assert_not_called()


def test_failed_native_action_restores_privilege_and_closes_token(api):
    native, dll, last = api

    def denied(*_args):
        last[0] = 5
        return 0

    dll.ExitWindowsEx.side_effect = denied
    with pytest.raises(OSError):
        native.perform("reboot")
    assert dll.AdjustTokenPrivileges.call_count == 2
    dll.CloseHandle.assert_called_once()


def test_already_enabled_privilege_needs_no_restore(api):
    native, dll, _last = api
    dll.AdjustTokenPrivileges.side_effect = None
    native.perform("reboot")
    assert dll.AdjustTokenPrivileges.call_count == 1
    dll.CloseHandle.assert_called_once()


def test_restore_success_with_not_all_assigned_is_reported(api, monkeypatch):
    native, dll, last = api
    original = dll.AdjustTokenPrivileges.side_effect
    warning = Mock()
    monkeypatch.setattr(windows.log, "warning", warning)

    def adjust(token, disable, new, capacity, old, size):
        if not old:
            last[0] = 1300
            return 1
        return original(token, disable, new, capacity, old, size)

    dll.AdjustTokenPrivileges.side_effect = adjust
    native.perform("reboot")
    warning.assert_called_once_with("Could not restore shutdown privilege: %s", 1300)
    dll.CloseHandle.assert_called_once()


def test_adjust_success_with_not_all_assigned_never_calls_shutdown(api):
    native, dll, last = api

    def missing(*_args):
        last[0] = 1300
        return 1

    dll.AdjustTokenPrivileges.side_effect = missing
    with pytest.raises(OSError) as exc:
        native.perform("poweroff")
    assert exc.value.errno == 1300
    dll.ExitWindowsEx.assert_not_called()
    dll.CloseHandle.assert_called_once()


def test_lock_and_invalid_actions_never_adjust_shutdown_right(api):
    native, dll, _last = api
    native.perform("lock")
    dll.LockWorkStation.assert_called_once_with()
    with pytest.raises(ValueError):
        native.perform("shell command")
    dll.AdjustTokenPrivileges.assert_not_called()
    dll.ExitWindowsEx.assert_not_called()


def test_request_worker_keeps_ui_responsive_and_suppresses_duplicate_requests():
    started, release = threading.Event(), threading.Event()

    def perform(_action):
        started.set()
        assert release.wait(3)

    native = SimpleNamespace(
        available_actions=lambda: {"suspend"}, perform=Mock(side_effect=perform)
    )
    value = windows.WindowsSessionBackend(api=native)
    try:
        assert value.perform("suspend")
        assert started.wait(1)
        assert not value.available_actions()
        assert not value.perform("suspend")
        value.stop()
        assert not value.perform("suspend")
    finally:
        release.set()
        value._worker.join(2)
    assert native.perform.call_count == 1


def test_worker_revalidates_availability_and_recovers_after_denial():
    native = SimpleNamespace(
        available_actions=Mock(side_effect=[{"reboot"}, set()]), perform=Mock()
    )
    value = windows.WindowsSessionBackend(api=native)
    assert value.perform("reboot")
    value._worker.join(2)
    native.perform.assert_not_called()
    native.available_actions.side_effect = None
    native.available_actions.return_value = {"reboot"}
    native.perform.side_effect = OSError(5, "denied")
    assert value.perform("reboot")
    value._worker.join(2)
    assert value.available_actions() == {"reboot"}


def test_worker_start_failure_leaves_actions_available(api, monkeypatch):
    native, _dll, _last = api
    worker = SimpleNamespace(start=Mock(side_effect=RuntimeError("thread unavailable")))
    monkeypatch.setattr(windows.threading, "Thread", lambda **_kw: worker)
    value = windows.WindowsSessionBackend(api=native)
    assert not value.perform("reboot")
    assert "reboot" in value.available_actions()


def test_native_capability_reads_do_not_leak_token_handles():
    import sys

    if sys.platform != "win32":
        pytest.skip("Native Windows session API")
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.GetProcessHandleCount.argtypes = (
        ctypes.c_void_p,
        ctypes.POINTER(windows._DWORD),
    )
    count = windows._DWORD()

    def handles():
        assert kernel.GetProcessHandleCount(
            kernel.GetCurrentProcess(), ctypes.byref(count)
        )
        return count.value

    native = windows._PowerAPI()
    native.available_actions()
    before = handles()
    for _ in range(40):
        assert native.available_actions() >= {"lock", "logout"}
    assert handles() <= before + 1


def test_native_existing_shutdown_privilege_is_restored_without_an_os_action():
    import os
    import subprocess
    import sys
    from pathlib import Path

    if sys.platform != "win32":
        pytest.skip("Native Windows token API")
    native = windows._PowerAPI()
    if not native._can_shutdown():
        pytest.skip("Account has no shutdown right")

    def privilege():
        result = subprocess.run(
            [
                str(Path(os.environ["SYSTEMROOT"]) / "System32/whoami.exe"),
                "/priv",
                "/fo",
                "csv",
                "/nh",
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return next(
            line for line in result.stdout.splitlines() if "SeShutdownPrivilege" in line
        )

    before = privilege()
    with native._shutdown_privilege():
        during = privilege()
    assert privilege() == before
    assert during
