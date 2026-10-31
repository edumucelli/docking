"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import shutil
import subprocess

from docking.applets.volume.operations import VolumeOperations as VolumeOperationsBase

from ...state import _VOLUME_SETTINGS_COMMANDS, log


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


class VolumeOperations(VolumeOperationsBase):
    def volume_settings_command(self) -> list[str] | None:
        return volume_settings_command()

    def open_volume_settings(self) -> bool:
        return open_volume_settings()
