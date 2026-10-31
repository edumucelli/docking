"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import shutil
import subprocess

from docking.applets.capslock.backend import (
    CapslockOperations as CapslockOperationsBase,
)

from ...state import LockKeyState, parse_xset_query


def query_lock_state() -> LockKeyState:
    """Query Caps/Num lock state via X11 ``xset q``."""
    if shutil.which("xset") is None:
        return LockKeyState(available=False)

    try:
        completed = subprocess.run(
            ["xset", "q"],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return LockKeyState(available=False)

    if completed.returncode != 0:
        return LockKeyState(available=False)
    return parse_xset_query(completed.stdout)


class CapslockOperations(CapslockOperationsBase):
    def query_lock_state(self) -> LockKeyState:
        return query_lock_state()
