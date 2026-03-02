"""State and command helpers for session applet."""

from __future__ import annotations

import subprocess
from typing import NamedTuple

from docking.applets.identity import AppletId
from docking.log import get_logger, with_context

_log = with_context(get_logger(name="session"), applet_id=str(AppletId.SESSION))


class SessionAction(NamedTuple):
    """A session/power action with its shell command."""

    label: str
    command: list[str]


_ACTIONS: list[SessionAction] = [
    SessionAction("Lock Screen", ["loginctl", "lock-session"]),
    SessionAction("Log Out", ["loginctl", "terminate-session", ""]),
    SessionAction("Suspend", ["systemctl", "suspend"]),
    SessionAction("Restart", ["systemctl", "reboot"]),
    SessionAction("Shut Down", ["systemctl", "poweroff"]),
]


def _run(*, cmd: list[str], action: str) -> None:
    """Run a session/power command, logging failures."""
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        _log.bind(action=action).warning(f"Failed to run {cmd}: {exc}")
