"""POSIX termination semantics with no real process signaling on either host."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.platform.backends.unix import processes
from docking.platform.backends.unix.processes import kill_pid


@pytest.fixture(autouse=True)
def unix_signals(monkeypatch):
    # These tests model a Unix backend even when pytest runs on Windows.
    monkeypatch.setattr(processes, "signal", SimpleNamespace(SIGKILL=9))


class TestKillPid:
    def test_returns_false_for_zero(self):
        assert kill_pid(pid=0) is False

    def test_returns_false_for_negative(self):
        assert kill_pid(pid=-1) is False

    def test_returns_false_on_os_error(self, monkeypatch):
        monkeypatch.setattr("os.kill", MagicMock(side_effect=OSError("no process")))
        assert kill_pid(pid=99999) is False

    def test_returns_true_on_success(self, monkeypatch):
        killed = MagicMock()
        monkeypatch.setattr("os.kill", killed)
        assert kill_pid(pid=1234) is True
        killed.assert_called_once_with(1234, 9)
