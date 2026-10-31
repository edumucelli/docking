"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import shutil
import subprocess

from docking.applets.network.operations import (
    NetworkOperations as NetworkOperationsBase,
)
from docking.platform.environment import flatpak

from ...state import _CONNECTION_INFO_COMMANDS, _EDIT_CONNECTIONS_COMMANDS, log


def connection_info_command() -> list[str] | None:
    """Return the first available desktop network-info/settings command."""
    return _available_desktop_command(_CONNECTION_INFO_COMMANDS)


def edit_connections_command() -> list[str] | None:
    """Return the first available network-connections editor command."""
    return _available_desktop_command(_EDIT_CONNECTIONS_COMMANDS)


def open_connection_info() -> bool:
    """Launch the desktop network information/settings tool when available."""
    return _open_command(cmd=connection_info_command(), action="open_connection_info")


def open_edit_connections() -> bool:
    """Launch the desktop network-connections editor when available."""
    return _open_command(
        cmd=edit_connections_command(),
        action="open_edit_connections",
    )


def open_hidden_wifi_settings() -> bool:
    """Launch the desktop network editor for hidden Wi-Fi setup."""
    return _open_command(
        cmd=edit_connections_command() or connection_info_command(),
        action="open_hidden_wifi_settings",
    )


def open_new_wifi_settings() -> bool:
    """Launch the desktop network editor for creating a new Wi-Fi network."""
    return _open_command(
        cmd=edit_connections_command() or connection_info_command(),
        action="open_new_wifi_settings",
    )


def _open_command(*, cmd: list[str] | None, action: str) -> bool:
    if cmd is None:
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        log.bind(action=action).warning("Failed to run %s: %s", cmd, exc)
        return False
    return True


def _available_desktop_command(
    candidates: tuple[tuple[str, ...], ...],
) -> list[str] | None:
    flatpak_spawn = flatpak.spawn_path()
    for cmd in candidates:
        if flatpak_spawn is not None:
            host_command = flatpak.host_command(list(cmd))
            if flatpak.host_command_available(cmd[0]) and host_command is not None:
                return host_command
            continue
        if shutil.which(cmd[0]):
            return list(cmd)
    return None


class NetworkOperations(NetworkOperationsBase):
    def connection_info_command(self) -> list[str] | None:
        return connection_info_command()

    def edit_connections_command(self) -> list[str] | None:
        return edit_connections_command()

    def open_connection_info(self) -> bool:
        return open_connection_info()

    def open_edit_connections(self) -> bool:
        return open_edit_connections()

    def open_hidden_wifi_settings(self) -> bool:
        return open_hidden_wifi_settings()

    def open_new_wifi_settings(self) -> bool:
        return open_new_wifi_settings()
