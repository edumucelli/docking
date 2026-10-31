"""Unix native operations extracted without changing fallback policy."""

from __future__ import annotations

import shutil
import subprocess

from docking.applets.bluetooth.operations import (
    BluetoothOperations as BluetoothOperationsBase,
)

from ...state import (
    _ADAPTERS_COMMANDS,
    _DEVICES_COMMANDS,
    _LOCAL_SERVICES_COMMANDS,
    _SEND_FILES_COMMANDS,
    log,
)


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


class BluetoothOperations(BluetoothOperationsBase):
    def send_files_command(self) -> list[str] | None:
        return send_files_command()

    def devices_command(self) -> list[str] | None:
        return devices_command()

    def adapters_command(self) -> list[str] | None:
        return adapters_command()

    def local_services_command(self) -> list[str] | None:
        return local_services_command()

    def open_send_files(self) -> bool:
        return open_send_files()

    def open_devices(self) -> bool:
        return open_devices()

    def open_adapters(self) -> bool:
        return open_adapters()

    def open_local_services(self) -> bool:
        return open_local_services()
