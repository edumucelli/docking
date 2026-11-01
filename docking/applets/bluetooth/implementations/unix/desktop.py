"""Bluetooth desktop tools used by Linux and BSD implementations."""

from __future__ import annotations

import shutil
import subprocess

from docking.applets.bluetooth.backend import BluetoothAction
from docking.log import get_logger

log = get_logger("bluetooth.unix_desktop")

_SEND_FILES_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("blueman-sendto",),
    ("bluetooth-sendto",),
    ("gnome-bluetooth-sendto",),
)
_DEVICES_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("blueman-manager",),
    ("gnome-control-center", "bluetooth"),
    ("mate-bluetooth-properties",),
    ("kcmshell6", "kcm_bluetooth"),
    ("kcmshell5", "kcm_bluetooth"),
)
_ADAPTERS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("blueman-adapters",),
    ("gnome-control-center", "bluetooth"),
    ("mate-bluetooth-properties",),
    ("kcmshell6", "kcm_bluetooth"),
    ("kcmshell5", "kcm_bluetooth"),
)
_LOCAL_SERVICES_COMMANDS: tuple[tuple[str, ...], ...] = (("blueman-services",),)


def send_files_command() -> list[str] | None:
    """Return the first available Bluetooth file-sender command."""
    return _first_available_command(_SEND_FILES_COMMANDS)


def devices_command() -> list[str] | None:
    """Return the first available Bluetooth devices/settings command."""
    return _first_available_command(_DEVICES_COMMANDS)


def adapters_command() -> list[str] | None:
    """Return the first available Bluetooth adapters/settings command."""
    return _first_available_command(_ADAPTERS_COMMANDS)


def local_services_command() -> list[str] | None:
    """Return the first available Bluetooth local-services command."""
    return _first_available_command(_LOCAL_SERVICES_COMMANDS)


def open_send_files() -> bool:
    """Launch the desktop Bluetooth file sender when available."""
    return _open_command(cmd=send_files_command(), action="open_send_files")


def open_devices() -> bool:
    """Launch the desktop Bluetooth devices/settings screen when available."""
    return _open_command(cmd=devices_command(), action="open_devices")


def open_adapters() -> bool:
    """Launch the desktop Bluetooth adapters/settings screen when available."""
    return _open_command(cmd=adapters_command(), action="open_adapters")


def open_local_services() -> bool:
    """Launch the desktop Bluetooth local-services screen when available."""
    return _open_command(
        cmd=local_services_command(),
        action="open_local_services",
    )


def _first_available_command(
    candidates: tuple[tuple[str, ...], ...],
) -> list[str] | None:
    for cmd in candidates:
        if shutil.which(cmd[0]):
            return list(cmd)
    return None


def _open_command(*, cmd: list[str] | None, action: str) -> bool:
    if cmd is None:
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        log.bind(action=action).warning("Failed to run %s: %s", cmd, exc)
        return False
    return True


def desktop_actions() -> tuple[BluetoothAction, ...]:
    return tuple(action for action, query in _COMMANDS.items() if query() is not None)


def open_desktop_action(action: BluetoothAction) -> bool:
    query = _COMMANDS.get(action)
    return _open_command(cmd=query(), action=action.value) if query else False


_COMMANDS = {
    BluetoothAction.SEND_FILES: send_files_command,
    BluetoothAction.DEVICES: devices_command,
    BluetoothAction.ADAPTERS: adapters_command,
    BluetoothAction.LOCAL_SERVICES: local_services_command,
}
