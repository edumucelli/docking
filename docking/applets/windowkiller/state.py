# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

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

from docking.log import get_logger

log = get_logger("windowkiller.state")


def kill_pid(pid: int) -> bool:
    """Send SIGKILL to a process. Returns True on success."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGKILL)
        return True
    except OSError as exc:
        log.debug("Failed to SIGKILL pid %s: %s", pid, exc)
        return False
