"""Power settings launchers shared by Linux and BSD implementations."""

import shutil
import subprocess

from docking.log import get_logger, with_context

log = with_context(get_logger("battery.desktop_settings"))

_POWER_SETTINGS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("gnome-control-center", "power"),
    ("mate-power-preferences",),
    ("xfce4-power-manager-settings",),
    ("kcmshell6", "powerdevilprofilesconfig"),
    ("kcmshell5", "powerdevilprofilesconfig"),
)


def power_settings_command() -> list[str] | None:
    """Return the first available desktop power-settings command."""
    for cmd in _POWER_SETTINGS_COMMANDS:
        if shutil.which(cmd[0]):
            return list(cmd)
    return None


def open_power_settings() -> bool:
    """Launch the desktop power-settings tool when one is available."""
    cmd = power_settings_command()
    if cmd is None:
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        log.bind(action="open_power_settings").warning("Failed to run %s: %s", cmd, exc)
        return False
    return True
