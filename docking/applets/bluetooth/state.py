# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""State and display helpers shared by Bluetooth backends."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from docking.applets.bluetooth import meta
from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(get_logger(name="bluetooth"), applet_id=meta.id)

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


@dataclass(frozen=True, slots=True)
class BluetoothAdapterState:
    path: str
    name: str
    alias: str
    powered: bool
    discovering: bool
    address: str


@dataclass(frozen=True, slots=True)
class BluetoothDeviceState:
    path: str
    adapter_path: str
    name: str
    alias: str
    address: str
    icon_name: str
    paired: bool
    trusted: bool
    connected: bool
    battery_percent: int | None
    rssi: int | None


@dataclass(frozen=True, slots=True)
class BluetoothState:
    available: bool
    adapters: tuple[BluetoothAdapterState, ...]
    devices: tuple[BluetoothDeviceState, ...]
    active_adapter_path: str
    error: str = ""


def unavailable_state(error: str = "") -> BluetoothState:
    return BluetoothState(
        available=False,
        adapters=(),
        devices=(),
        active_adapter_path="",
        error=error,
    )


def connected_count(state: BluetoothState) -> int:
    return sum(1 for device in state.devices if device.connected)


def adapter_from_state(
    state: BluetoothState,
    preferred_path: str | None,
) -> str | None:
    if not state.adapters:
        return None
    if preferred_path and any(a.path == preferred_path for a in state.adapters):
        return preferred_path
    powered = [adapter for adapter in state.adapters if adapter.powered]
    if powered:
        return powered[0].path
    return state.adapters[0].path


def build_tooltip(state: BluetoothState, active_adapter_path: str) -> str:
    if not state.available:
        if state.error:
            return _("Bluetooth: {error}").format(error=state.error)
        return _("Bluetooth: No adapter/service")

    adapter = _find_adapter(state=state, path=active_adapter_path)
    if adapter is None:
        return _("Bluetooth: No adapter/service")

    powered = _("On") if adapter.powered else _("Off")
    paired = [d for d in state.devices if d.adapter_path == adapter.path and d.paired]
    connected = [d for d in paired if d.connected]

    lines = [
        _("Bluetooth: {state}").format(state=powered),
        _("Adapter: {name}").format(name=adapter.alias or adapter.name),
        _("Connected: {n}").format(n=len(connected)),
        _("Paired: {n}").format(n=len(paired)),
        _("Discovering: {state}").format(
            state=_("Yes") if adapter.discovering else _("No")
        ),
    ]

    device_with_battery = next(
        (d for d in connected if d.battery_percent is not None),
        None,
    )
    if device_with_battery and device_with_battery.battery_percent is not None:
        lines.append(
            f"Battery: {_device_display_name(device_with_battery)} "
            f"{device_with_battery.battery_percent}%"
        )
    return "\n".join(lines)


def device_menu_label(device: BluetoothDeviceState) -> str:
    base = _device_display_name(device=device)
    tags: list[str] = []
    if device.connected:
        tags.append("Connected")
    if device.paired:
        tags.append("Paired")
    if device.battery_percent is not None:
        tags.append(f"{device.battery_percent}%")
    if not tags:
        return base
    return f"{base} ({', '.join(tags)})"


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


def _device_sort_key(device: BluetoothDeviceState) -> tuple[int, int, str]:
    return (
        0 if device.connected else 1,
        0 if device.paired else 1,
        _device_display_name(device=device).lower(),
    )


def _find_adapter(state: BluetoothState, path: str) -> BluetoothAdapterState | None:
    return next((adapter for adapter in state.adapters if adapter.path == path), None)


def _device_display_name(device: BluetoothDeviceState) -> str:
    return (
        device.alias or device.name or device.address or device.path.rsplit("/", 1)[-1]
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
