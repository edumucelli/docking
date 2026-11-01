"""X11 lock-key command failures leave the state unavailable."""

import subprocess
from unittest.mock import Mock

from docking.applets.capslock.implementations.unix import backend as linux
from docking.applets.capslock.state import LockKeyState


def test_x11_timeout_does_not_block_or_reuse_previous_state(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", lambda _: "/usr/bin/xset")
    run = Mock(side_effect=subprocess.TimeoutExpired("xset", 1.0))
    monkeypatch.setattr(linux.subprocess, "run", run)
    assert linux.X11LockKeyBackend().get_state() == LockKeyState(False)
    assert run.call_args.kwargs["timeout"] == 1.0
