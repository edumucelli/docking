"""Device snapshots and presentation without GTK or D-Bus dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docking.core.paths import local_path_from_uri_or_path
from docking.i18n import _

BATTERY_PLUGIN = "kdeconnect_battery"
RING_PLUGIN = "kdeconnect_findmyphone"
SHARE_PLUGIN = "kdeconnect_share"


@dataclass(frozen=True, slots=True)
class Device:
    device_id: str
    name: str
    kind: str = "phone"
    reachable: bool = False
    plugins: frozenset[str] = frozenset()
    battery: int | None = None
    charging: bool = False

    def supports(self, plugin: str) -> bool:
        return self.reachable and plugin in self.plugins


@dataclass(frozen=True, slots=True)
class ConnectState:
    available: bool = False
    devices: tuple[Device, ...] = ()
    error: str = ""


def selected_device(state: ConnectState, selected: str) -> Device | None:
    if not state.available:
        return None
    # Keep the user's selection even when it is offline. Never silently send
    # a file to a different device because the selected phone disconnected.
    if selected:
        return next((d for d in state.devices if d.device_id == selected), None)
    return next((d for d in state.devices if d.reachable), None) or next(
        iter(state.devices), None
    )


def device_label(device: Device) -> str:
    if not device.reachable:
        return _("{name}: offline").format(name=device.name)
    if device.battery is None:
        return _("{name}: connected").format(name=device.name)
    if device.charging:
        return _("{name}: {charge}% (charging)").format(
            name=device.name, charge=device.battery
        )
    return _("{name}: {charge}%").format(name=device.name, charge=device.battery)


def tooltip(state: ConnectState, selected: str, action_status: str = "") -> str:
    lines = [_("KDE Connect")]
    if not state.available:
        lines.append(
            state.error or _("Install and start KDE Connect to connect a phone")
        )
    elif not state.devices:
        lines.append(_("No paired devices; open KDE Connect to pair your phone"))
    else:
        device = selected_device(state, selected)
        if device is None:
            lines.append(
                _("Selected device is no longer paired; choose another device")
            )
        else:
            lines.append(device_label(device))
            if device.supports(SHARE_PLUGIN):
                lines.append(_("Drop files here to send to this device"))
        lines.extend(device_label(d) for d in state.devices if d != device)
    if action_status:
        lines.append(action_status)
    return "\n".join(lines)


def selected_from_prefs(raw: Any) -> str:
    value = raw.get("selected_device", "") if isinstance(raw, dict) else ""
    return value if isinstance(value, str) else ""


def local_files(uris: list[str]) -> tuple[Path, ...]:
    """Accept a whole explicit batch of local regular files, never directories."""
    if not uris:
        raise ValueError(_("Choose at least one local file"))
    paths: list[Path] = []
    for uri in uris:
        path = local_path_from_uri_or_path(uri)
        if path is None or not path.is_file():
            raise ValueError(_("Only local files can be sent with KDE Connect"))
        path = path.resolve()
        if path not in paths:
            paths.append(path)
    return tuple(paths)
