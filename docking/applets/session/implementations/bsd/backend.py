"""BSD session-action commands, with privileges controlled by the OS."""

import shutil

from docking.applets.session.backend import SessionBackend
from docking.applets.session.implementations.unix.actions import SessionCommands
from docking.platform.environment import is_netbsd, is_openbsd

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
    if is_netbsd():
        from . import netbsd as native
    elif is_openbsd():
        from . import openbsd as native
    else:
        from . import freebsd as native
    commands = {
        "suspend": list(native.SUSPEND_COMMAND),
        "reboot": ["shutdown", "-r", "now"],
        "poweroff": list(native.POWEROFF_COMMAND),
    }
    result = commands.get(action)
    return result if result and shutil.which(result[0]) else None


class BsdSessionBackend(SessionBackend, SessionCommands):
    def command_for(self, action: str) -> list[str] | None:
        return command_for(action)

    def lock_commands(self) -> tuple[tuple[str, ...], ...]:
        return _LOCK_SCREEN_COMMAND_CANDIDATES

    def available_actions(self) -> tuple[str, ...]:
        return ("lock", "logout", "suspend", "reboot", "poweroff")

    def perform(self, action: str) -> bool:
        from ..unix.actions import lock_screen, perform

        return lock_screen(self) if action == "lock" else perform(self, action=action)

    def stop(self) -> None:
        # Commands complete synchronously; no resources remain owned here.
        pass
