"""Pure logic for Window Killer applet -- no GTK dependency."""

from __future__ import annotations

import os
import signal


def kill_pid(pid: int) -> bool:
    """Send SIGKILL to a process. Returns True on success."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except OSError:
        return False
