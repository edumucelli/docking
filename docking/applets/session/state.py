"""State and command helpers for session applet."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import NamedTuple

from docking.applets.session import meta
from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(get_logger(name="session"), applet_id=meta.id)


class SessionAction(NamedTuple):
    """A session/power action with its shell command."""

    label: str
    command: list[str]


LOCK_SCREEN_LABEL = _("Lock Screen")
_ACTIONS: list[SessionAction] = [
    SessionAction(LOCK_SCREEN_LABEL, ["loginctl", "lock-session"]),
    SessionAction(_("Log Out"), ["loginctl", "terminate-session", ""]),
    SessionAction(_("Suspend"), ["systemctl", "suspend"]),
    SessionAction(_("Restart"), ["systemctl", "reboot"]),
    SessionAction(_("Shut Down"), ["systemctl", "poweroff"]),
]

_LOCK_SCREEN_COMMAND_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("mate-screensaver-command", "-l"),
    ("gnome-screensaver-command", "-l"),
    ("xdg-screensaver", "lock"),
    ("dm-tool", "lock"),
    ("loginctl", "lock-session"),
)


def lock_screen() -> bool:
    """Lock the active session with explicit session-id handling and fallback."""
    session_id = (os.environ.get("XDG_SESSION_ID") or "").strip()
    commands = _lock_screen_commands(session_id=session_id)
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.bind(action="lock_screen").debug("Failed to run %s: %s", cmd, exc)
            continue
        if result.returncode == 0:
            return True
        log.bind(action="lock_screen").debug(
            "Lock command failed: %s (rc=%s, stderr=%s)",
            cmd,
            result.returncode,
            result.stderr.strip(),
        )
    log.bind(action="lock_screen").warning("Failed to lock screen.")
    return False


def _run(*, cmd: list[str], action: str) -> None:
    """Run a session/power command, logging failures."""
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        log.bind(action=action).warning(f"Failed to run {cmd}: {exc}")


def _lock_screen_commands(*, session_id: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for cmd in _LOCK_SCREEN_COMMAND_CANDIDATES:
        if shutil.which(cmd[0]) is None:
            continue
        if cmd[0] == "loginctl" and session_id:
            commands.append([cmd[0], cmd[1], session_id])
        commands.append(list(cmd))
    return commands
