"""Bounded audio command execution shared by the volume integrations."""

import os
import subprocess

from docking.applets.volume import meta
from docking.log import get_logger, with_context

log = with_context(get_logger(name="volume"), applet_id=meta.id)


def run_command(cmd: list[str], action: str) -> str | None:
    """Run command, return stdout or None on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=2,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode == 0:
            return result.stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.bind(action=action).warning(f"Failed to run {cmd}: {exc}")
    return None
