"""Process-termination helper for the Window Killer applet.

This module is intentionally tiny because it wraps a high-consequence action:
``SIGKILL``. Keeping that action in its own GTK-free helper makes the contract
obvious:

- invalid or non-positive PIDs are rejected,
- a single kill attempt is made,
- success/failure is reported as a boolean.

The applet layer is responsible for deciding *which* window/process should be
targeted. This module is responsible only for the final operating-system call.
That separation keeps the dangerous edge of the feature easy to audit.
"""

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
