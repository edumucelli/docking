"""X11 keyboard lock indicators through the existing xset command."""

import re
import shutil
import subprocess

from docking.applets.capslock.backend import LockKeyBackend
from docking.applets.capslock.state import LockKeyState

_INDICATOR_RE = re.compile(
    r"\b(?P<index>\d+):\s*(?P<name>Caps Lock|Num Lock):\s*(?P<state>on|off)\b",
    re.IGNORECASE,
)


class X11LockKeyBackend(LockKeyBackend):
    def get_state(self) -> LockKeyState:
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


def parse_xset_query(output: str) -> LockKeyState:
    """Parse ``xset q`` output for Caps Lock and Num Lock indicators."""
    values: dict[str, bool] = {}
    for match in _INDICATOR_RE.finditer(output):
        name = match.group("name").lower()
        values[name] = match.group("state").lower() == "on"

    if "caps lock" not in values or "num lock" not in values:
        return LockKeyState(available=False)
    return LockKeyState(
        available=True,
        caps_lock=values["caps lock"],
        num_lock=values["num lock"],
    )
