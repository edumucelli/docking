"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import shutil
import subprocess

from ...state import _POWER_SETTINGS_COMMANDS, log


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


class BatteryOperations:
    def power_settings_command(self) -> list[str] | None:
        return power_settings_command()

    def open_power_settings(self) -> bool:
        return open_power_settings()
