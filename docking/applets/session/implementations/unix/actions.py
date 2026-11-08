from __future__ import annotations

import os
import shutil
import subprocess
from abc import ABC, abstractmethod

from docking.log import get_logger, with_context
from docking.platform.environment import flatpak

from ...state import _LOCK_SCREEN_DBUS_CANDIDATES

log = with_context(get_logger("session"))


class SessionCommands(ABC):
    @abstractmethod
    def command_for(self, action: str) -> list[str] | None: ...
    @abstractmethod
    def lock_commands(self) -> tuple[tuple[str, ...], ...]: ...


def lock_screen(backend: SessionCommands) -> bool:
    """Lock the active session with explicit session-id handling and fallback."""
    session_id = (os.environ.get("XDG_SESSION_ID") or "").strip()
    commands = _lock_screen_commands(backend, session_id=session_id)
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            log.bind(action="lock_screen").debug("Failed to run %s: %s", cmd, exc)
            continue
        if result.returncode == 0:
            return True
        log.bind(action="lock_screen").debug(
            "Lock command failed: %s (rc=%s, stderr=%s)",
            cmd,
            result.returncode,
            result.stderr.strip(),
        )
    log.bind(action="lock_screen").warning("Failed to lock screen.")
    return False


def perform(backend: SessionCommands, *, action: str) -> bool:
    """Run a semantic session action through the selected OS backend."""
    resolved_cmd = backend.command_for(action)
    if not resolved_cmd:
        log.bind(action=action).warning("Session action unavailable: %s", action)
        return False
    resolved_cmd = flatpak.host_command(resolved_cmd) or resolved_cmd
    resolved_cmd = [a for a in resolved_cmd if a != ""]
    try:
        subprocess.Popen(resolved_cmd)
        return True
    except OSError as exc:
        log.bind(action=action).warning(f"Failed to run {resolved_cmd}: {exc}")
        return False


def _lock_screen_commands(
    backend: SessionCommands, *, session_id: str
) -> list[list[str]]:
    commands: list[list[str]] = []
    gdbus = shutil.which("gdbus")
    if gdbus is not None:
        for destination, object_path, method in _LOCK_SCREEN_DBUS_CANDIDATES:
            commands.append(
                [
                    gdbus,
                    "call",
                    "--session",
                    "--dest",
                    destination,
                    "--object-path",
                    object_path,
                    "--method",
                    method,
                ]
            )
    flatpak_spawn = flatpak.spawn_path()
    candidates = backend.lock_commands()
    for cmd in candidates:
        if flatpak_spawn is None and shutil.which(cmd[0]) is None:
            continue
        if cmd[0] == "loginctl" and session_id:
            loginctl_cmd = [cmd[0], cmd[1], session_id]
            commands.append(flatpak.host_command(loginctl_cmd) or loginctl_cmd)
        candidate_cmd = list(cmd)
        commands.append(flatpak.host_command(candidate_cmd) or candidate_cmd)
    return commands
