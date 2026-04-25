"""Keyboard lock indicator detection for Caps Lock applet."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

from docking.i18n import _

POLL_INTERVAL_S = 1

_INDICATOR_RE = re.compile(
    r"\b(?P<index>\d+):\s*(?P<name>Caps Lock|Num Lock):\s*"
    r"(?P<state>on|off)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class LockKeyState:
    """Current state of the keyboard lock indicators."""

    available: bool
    caps_lock: bool = False
    num_lock: bool = False


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


def state_label(state: LockKeyState) -> str:
    """Compact icon label for the active lock state."""
    if not state.available:
        return "??"
    if state.caps_lock and state.num_lock:
        return "CN"
    if state.caps_lock:
        return "CAP"
    if state.num_lock:
        return "NUM"
    return "--"


def tooltip_text(state: LockKeyState) -> str:
    """Build user-facing tooltip text."""
    if not state.available:
        return _("Keyboard lock state unavailable")

    return "\n".join(
        [
            _("Caps Lock"),
            _("Caps Lock: {state}").format(state=_on_off(state.caps_lock)),
            _("Num Lock: {state}").format(state=_on_off(state.num_lock)),
        ]
    )


def menu_label(name: str, active: bool) -> str:
    """Build a lock-state menu label."""
    return _("{name}: {state}").format(name=name, state=_on_off(active))


def _on_off(active: bool) -> str:
    return _("On") if active else _("Off")
