"""Native toggle semantics, backend selection and lazy Windows ABI binding."""

import ctypes
import subprocess
from unittest.mock import Mock

import pytest

from docking.applets.capslock import backend, linux, windows
from docking.applets.capslock.state import LockKeyState


@pytest.mark.parametrize("native", [False, True])
def test_factory_selects_one_backend_without_querying_native_state(monkeypatch, native):
    monkeypatch.setattr(backend.environment, "is_windows", lambda: native)
    dll = Mock(side_effect=AssertionError("factory must not load Win32"))
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    reader = backend.create_backend()
    expected = windows.WindowsLockKeyBackend if native else linux.X11LockKeyBackend
    assert isinstance(reader, expected)
    dll.assert_not_called()


@pytest.mark.parametrize(
    "caps,num,expected",
    [
        (0, 0, LockKeyState(True)),
        (1, 0, LockKeyState(True, True, False)),
        (0, 1, LockKeyState(True, False, True)),
        (1, 1, LockKeyState(True, True, True)),
        (-32768, -32768, LockKeyState(True, False, False)),
        (-32767, -32767, LockKeyState(True, True, True)),
    ],
)
def test_windows_reads_toggle_bits_instead_of_currently_pressed_bits(
    caps, num, expected
):
    read = Mock(side_effect=[caps, num])
    assert windows.WindowsLockKeyBackend(read=read).get_state() == expected
    assert [call.args for call in read.call_args_list] == [(0x14,), (0x90,)]


def test_binding_uses_signed_short_and_reuses_the_reader(monkeypatch):
    user = Mock()
    user.GetKeyState.side_effect = [1, 0, 0, 1]
    dll = Mock(return_value=user)
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    reader = windows.WindowsLockKeyBackend()
    assert reader.get_state() == LockKeyState(True, True, False)
    assert reader.get_state() == LockKeyState(True, False, True)
    assert user.GetKeyState.restype is ctypes.c_int16
    assert user.GetKeyState.argtypes == (ctypes.c_int32,)
    dll.assert_called_once_with("user32", use_last_error=True)


def test_unavailable_binding_retries_and_partial_reads_are_not_published(monkeypatch):
    user = Mock()
    user.GetKeyState.side_effect = [1, OSError("second query failed"), 0, 1]
    dll = Mock(side_effect=[OSError("unavailable"), user])
    monkeypatch.setattr(ctypes, "WinDLL", dll, raising=False)
    reader = windows.WindowsLockKeyBackend()
    assert reader.get_state() == LockKeyState(False)
    assert reader.get_state() == LockKeyState(False)
    assert reader.get_state() == LockKeyState(True, False, True)
    assert dll.call_count == 2


def test_x11_timeout_does_not_block_or_reuse_previous_state(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", lambda _: "/usr/bin/xset")
    run = Mock(side_effect=subprocess.TimeoutExpired("xset", 1.0))
    monkeypatch.setattr(linux.subprocess, "run", run)
    assert linux.X11LockKeyBackend().get_state() == LockKeyState(False)
    assert run.call_args.kwargs["timeout"] == 1.0
