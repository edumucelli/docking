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

"""POSIX process termination shared by Unix window-picking implementations."""

from __future__ import annotations

import os
import signal

from docking.log import get_logger

log = get_logger("backend.unix.processes")


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
