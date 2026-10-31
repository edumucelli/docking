"""Command-based Unix implementation of semantic session actions."""

from __future__ import annotations

import os
import shutil
import subprocess
from abc import abstractmethod

from docking.applets.session.backend import SessionBackend
from docking.log import get_logger
from docking.platform.environment import flatpak

log = get_logger("session.unix")
_LOCK_SCREEN_DBUS_CANDIDATES = (
    ("org.mate.ScreenSaver", "/org/mate/ScreenSaver", "org.mate.ScreenSaver.Lock"),
    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.Lock"),
    (
        "org.freedesktop.ScreenSaver",
        "/org/freedesktop/ScreenSaver",
        "org.freedesktop.ScreenSaver.Lock",
    ),
)


class CommandSessionBackend(SessionBackend):
    @abstractmethod
    def command_for(self, action: str) -> list[str] | None: ...

    @abstractmethod
    def lock_commands(self) -> tuple[tuple[str, ...], ...]: ...

    def available_actions(self) -> frozenset[str]:
        actions = {
            action
            for action in ("logout", "suspend", "reboot", "poweroff")
            if (cmd := self.command_for(action))
            and (flatpak.spawn_path() or shutil.which(cmd[0]))
        }
        if self._lock_screen_commands():
            actions.add("lock")
        return frozenset(actions)

    def perform(self, action: str) -> bool:
        if action == "lock":
            return self.lock_screen()
        cmd = self.command_for(action)
        if not cmd:
            return False
        cmd = flatpak.host_command(cmd) or cmd
        cmd = [a for a in cmd if a]
        try:
            subprocess.Popen(cmd)
            return True
        except OSError as exc:
            log.warning("Failed to run %s: %s", cmd, exc)
            return False

    def lock_screen(self) -> bool:
        for cmd in self._lock_screen_commands():
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=5, check=False
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.debug("Failed to run %s: %s", cmd, exc)
                continue
            if result.returncode == 0:
                return True
            log.debug("Lock command failed: %s (rc=%s)", cmd, result.returncode)
        log.warning("Failed to lock screen.")
        return False

    def _lock_screen_commands(self) -> list[list[str]]:
        commands = []
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
        session_id = (os.environ.get("XDG_SESSION_ID") or "").strip()
        for cmd in self.lock_commands():
            if flatpak.spawn_path() is None and shutil.which(cmd[0]) is None:
                continue
            if cmd[0] == "loginctl" and session_id:
                explicit = [cmd[0], cmd[1], session_id]
                commands.append(flatpak.host_command(explicit) or explicit)
            candidate = list(cmd)
            commands.append(flatpak.host_command(candidate) or candidate)
        return commands
