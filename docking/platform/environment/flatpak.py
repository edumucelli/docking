"""Helpers for crossing the Flatpak sandbox boundary."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from collections.abc import Sequence

from docking.platform.environment import is_flatpak

HOST_ENV_UNSET = (
    "GIO_USE_VFS",
    "GI_TYPELIB_PATH",
    "GSETTINGS_SCHEMA_DIR",
    "XDG_DATA_DIRS",
)


def spawn_path(*, require_flatpak: bool = True) -> str | None:
    """Return flatpak-spawn path when running sandboxed and available."""
    if require_flatpak and not is_flatpak():
        return None
    return shutil.which("flatpak-spawn")


def host_command(
    cmd: Sequence[str],
    *,
    sanitize_env: bool = True,
    require_flatpak: bool = True,
) -> list[str] | None:
    """Build a flatpak-spawn --host command, or None outside Flatpak."""
    flatpak_spawn = spawn_path(require_flatpak=require_flatpak)
    if flatpak_spawn is None:
        return None

    command = [flatpak_spawn, "--host"]
    if sanitize_env:
        # Host GLib/GSettings commands can break if they inherit sandbox
        # typelibs, schemas, or XDG search paths from the Flatpak runtime.
        for name in HOST_ENV_UNSET:
            command.extend(["-u", name])
        command.insert(2, "env")
    command.extend(cmd)
    return command


def host_command_available(
    command: str,
    *,
    timeout: float = 1.5,
    require_flatpak: bool = True,
) -> bool:
    """Return True when *command* exists on the host."""
    cmd = host_command(
        ["sh", "-lc", f"command -v {shlex.quote(command)} >/dev/null"],
        sanitize_env=False,
        require_flatpak=require_flatpak,
    )
    if cmd is None:
        return shutil.which(command) is not None
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0
