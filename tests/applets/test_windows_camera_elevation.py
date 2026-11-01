"""Fixed-action elevation, cancellation and native process-handle ownership."""

import ctypes
from contextlib import contextmanager

import pytest

from docking.applets.camshield.implementations.windows import elevation as module


class Runtime:
    def __init__(self, monkeypatch):
        self.error = 0
        self.launch = True
        self.waits = [0]
        self.code = 0
        self.closed = []
        self.terminated = []
        self.arguments = None
        monkeypatch.setattr(
            module.ctypes, "WinDLL", lambda *a, **k: a[0], raising=False
        )
        monkeypatch.setattr(
            module.ctypes, "get_last_error", lambda: self.error, raising=False
        )
        monkeypatch.setattr(
            module.ctypes,
            "WinError",
            lambda code: OSError(code, "native failure"),
            raising=False,
        )
        monkeypatch.setattr(module, "bind", self.bind)

        @contextmanager
        def apartment():
            yield
            self.error = 999  # COM teardown must not erase a cancellation code.

        monkeypatch.setattr(module, "apartment", apartment)

    def bind(self, library, name, result, *kinds):
        if name == "IsUserAnAdmin":
            return lambda: False
        if name == "ShellExecuteExW":

            def execute(pointer):
                info = ctypes.cast(
                    pointer, ctypes.POINTER(module._ExecuteInfo)
                ).contents
                self.arguments = (info.verb, info.file, info.parameters, info.mask)
                if self.launch:
                    info.process = 91
                    return True
                return False

            return execute
        if name == "WaitForSingleObject":
            return lambda *args: self.waits.pop(0)
        if name == "GetExitCodeProcess":

            def exit_code(handle, output):
                ctypes.cast(output, ctypes.POINTER(ctypes.c_uint32))[0] = self.code
                return True

            return exit_code
        if name == "CloseHandle":
            return self.closed.append
        if name == "TerminateProcess":
            return lambda handle, code: self.terminated.append((handle, code)) or True
        raise AssertionError(name)


@pytest.fixture
def runtime(monkeypatch):
    return Runtime(monkeypatch)


def test_elevation_uses_fixed_action_and_closes_the_owned_process(runtime):
    assert module.run_action("lock")
    verb, _file, parameters, mask = runtime.arguments
    assert verb == "runas" and parameters.endswith(" lock") and mask & 0x40
    assert runtime.closed == [91]


def test_cancelled_uac_changes_no_device_and_preserves_native_error(runtime):
    runtime.launch = False
    runtime.error = 1223
    assert not module.run_action("unlock")
    assert not runtime.closed and not runtime.terminated


def test_failed_helper_and_wait_deadline_cannot_report_success(runtime):
    runtime.code = 1
    assert not module.run_action("lock") and runtime.closed == [91]
    runtime.waits = [0x102, 0]
    assert not module.run_action("unlock")
    assert runtime.terminated == [(91, 124)] and runtime.closed == [91, 91]


def test_failed_wait_still_closes_handle(runtime):
    runtime.waits = [0xFFFFFFFF]
    with pytest.raises(OSError):
        module.run_action("lock")
    assert runtime.closed == [91]


def test_unknown_action_never_enters_shell(runtime):
    assert not module.run_action("lock & something")
    assert runtime.arguments is None
