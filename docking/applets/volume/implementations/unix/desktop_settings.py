"""Sound settings launchers shared by Linux and BSD implementations."""

import shutil
import subprocess

from docking.applets.volume.backend import VolumeSettings
from docking.log import get_logger, with_context

log = with_context(get_logger("volume.desktop_settings"))

_VOLUME_SETTINGS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("gnome-control-center", "sound"),
    ("mate-volume-control",),
    ("xfce4-mixer-settings",),
    ("pavucontrol",),
    ("kcmshell6", "kcm_pulseaudio"),
    ("kcmshell5", "kcm_pulseaudio"),
)


def volume_settings_command() -> list[str] | None:
    """Return the first available desktop volume-settings command."""
    for cmd in _VOLUME_SETTINGS_COMMANDS:
        if shutil.which(cmd[0]):
            return list(cmd)
    return None


def open_volume_settings() -> bool:
    """Launch the desktop volume-settings tool when one is available."""
    cmd = volume_settings_command()
    if cmd is None:
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        log.bind(action="open_volume_settings").warning(
            "Failed to run %s: %s", cmd, exc
        )
        return False
    return True


SETTINGS = VolumeSettings(
    lambda: volume_settings_command() is not None, open_volume_settings
)
