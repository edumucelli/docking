"""State and command helpers for session applet."""

from __future__ import annotations

import subprocess
from typing import NamedTuple

from docking.applets.session import meta
from docking.i18n import _
from docking.log import get_logger, with_context

_log = with_context(get_logger(name="session"), applet_id=meta.id)


class SessionAction(NamedTuple):
    """A session/power action with its shell command."""

    label: str
    command: list[str]


_ACTIONS: list[SessionAction] = [
    SessionAction(_("Lock Screen"), ["loginctl", "lock-session"]),
    SessionAction(_("Log Out"), ["loginctl", "terminate-session", ""]),
    SessionAction(_("Suspend"), ["systemctl", "suspend"]),
    SessionAction(_("Restart"), ["systemctl", "reboot"]),
    SessionAction(_("Shut Down"), ["systemctl", "poweroff"]),
]


def _run(*, cmd: list[str], action: str) -> None:
    """Run a session/power command, logging failures."""
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        _log.bind(action=action).warning(f"Failed to run {cmd}: {exc}")
