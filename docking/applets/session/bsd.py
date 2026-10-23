"""BSD session-action commands, with privileges controlled by the OS."""

import shutil
import sys


def session_command(original: list[str]) -> list[str] | None:
    """Translate session actions; leave privilege decisions to the native OS."""
    if original[:2] == ["loginctl", "terminate-session"]:
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
        ("systemctl", "suspend"): ["zzz"] if openbsd else ["acpiconf", "-s", "3"],
        ("systemctl", "reboot"): ["shutdown", "-r", "now"],
        ("systemctl", "poweroff"): ["shutdown", "-hp" if openbsd else "-p", "now"],
    }
    result = commands.get(tuple(original[:2]))
    return result if result and shutil.which(result[0]) else None
