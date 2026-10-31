"""Linux session commands and screen-lock tools."""

import os
import shutil
from pathlib import Path

from docking.applets.session.backend import SessionBackend
from docking.applets.session.implementations.unix.actions import SessionCommands

_LOCK_SCREEN_COMMAND_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("mate-screensaver-command", "-l"),
    ("gnome-screensaver-command", "-l"),
    ("xdg-screensaver", "lock"),
    ("dm-tool", "lock"),
    ("loginctl", "lock-session"),
)


def _current_session_id() -> str:
    """Return the logind session ID for the current process.

    Tries ``XDG_SESSION_ID`` first, then ``/proc/self/sessionid``.
    Returns an empty string when neither source is available.
    """
    env_id = (os.environ.get("XDG_SESSION_ID") or "").strip()
    if env_id:
        return env_id
    try:
        proc_id = Path("/proc/self/sessionid").read_text().strip()
        if proc_id:
            return proc_id
    except (OSError, PermissionError):
        pass
    return ""


def command_for(action: str) -> list[str] | None:
    if action == "logout":
        if shutil.which("gnome-session-quit"):
            return ["gnome-session-quit", "--logout"]
        return ["loginctl", "terminate-session", _current_session_id()]
    return {
        "suspend": ["systemctl", "suspend"],
        "reboot": ["systemctl", "reboot"],
        "poweroff": ["systemctl", "poweroff"],
    }.get(action)


class LinuxSessionBackend(SessionBackend, SessionCommands):
    def command_for(self, action: str) -> list[str] | None:
        return command_for(action)

    def lock_commands(self) -> tuple[tuple[str, ...], ...]:
        return _LOCK_SCREEN_COMMAND_CANDIDATES

    def available_actions(self) -> tuple[str, ...]:
        return ("lock", "logout", "suspend", "reboot", "poweroff")

    def perform(self, action: str) -> bool:
        from ..unix.actions import lock_screen, perform

        return lock_screen(self) if action == "lock" else perform(self, action=action)
