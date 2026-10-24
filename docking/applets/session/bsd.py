"""BSD session-action commands, with privileges controlled by the OS."""

import shutil
import sys

from docking.applets.session.backend import SessionBackend

_LOCK_SCREEN_COMMAND_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("mate-screensaver-command", "-l"),
    ("gnome-screensaver-command", "-l"),
    ("xdg-screensaver", "lock"),
    ("dm-tool", "lock"),
    ("xfce4-screensaver-command", "--lock"),
    ("xscreensaver-command", "-lock"),
    ("xflock4",),
)


def command_for(action: str) -> list[str] | None:
    """Translate session actions; leave privilege decisions to the native OS."""
    if action == "logout":
        for argv in (
            ("xfce4-session-logout", "--logout"),
            ("mate-session-save", "--logout-dialog"),
            ("gnome-session-quit", "--logout"),
        ):
            if shutil.which(argv[0]):
                return list(argv)
        return None
    openbsd = sys.platform.startswith("openbsd")
    commands = {
        "suspend": ["zzz"] if openbsd else ["acpiconf", "-s", "3"],
        "reboot": ["shutdown", "-r", "now"],
        "poweroff": ["shutdown", "-hp" if openbsd else "-p", "now"],
    }
    result = commands.get(action)
    return result if result and shutil.which(result[0]) else None


class BsdSessionBackend(SessionBackend):
    def command_for(self, action: str) -> list[str] | None:
        return command_for(action)

    def lock_commands(self) -> tuple[tuple[str, ...], ...]:
        return _LOCK_SCREEN_COMMAND_CANDIDATES
