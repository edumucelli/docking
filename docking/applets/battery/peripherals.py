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

"""Peripheral battery discovery via UPower (mouse, keyboard, headset, ...).

UPower exposes wireless peripherals on the system bus alongside the laptop
battery. We enumerate devices and keep the ones that are *not* a power supply
(``PowerSupply == false``) and whose ``Type`` is an actual accessory, then read
each device's charge. The laptop battery, UPS, line power, monitors and the
aggregate ``DisplayDevice`` are filtered out.

Parsing/formatting is split from the DBus access so it can be unit-tested with
plain property dicts; the Gio call returns ``[]`` on any failure (e.g. a
headless session with no system bus).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple

from gi.repository import Gio, GLib

from docking.i18n import _
from docking.log import get_logger

log = get_logger("battery.peripherals")

_UPOWER = "org.freedesktop.UPower"
_UPOWER_PATH = "/org/freedesktop/UPower"
_UPOWER_DEVICE = "org.freedesktop.UPower.Device"
_DBUS_TIMEOUT_MS = 2000

# UPower device Type enum -> human label, restricted to real accessories.
_TYPE_NAMES: dict[int, str] = {
    5: _("Mouse"),
    6: _("Keyboard"),
    8: _("Phone"),
    9: _("Media Player"),
    10: _("Tablet"),
    12: _("Controller"),
    13: _("Pen"),
    14: _("Touchpad"),
    17: _("Headset"),
    18: _("Speakers"),
    19: _("Headphones"),
    21: _("Audio"),
    22: _("Remote"),
    26: _("Wearable"),
}
# Types that are never peripherals: unknown, line-power, battery, ups, monitor,
# computer, network.
_EXCLUDED_TYPES = frozenset({0, 1, 2, 3, 4, 11, 16})

# UPower State enum value for charging.
_STATE_CHARGING = 1
# UPower BatteryLevel enum -> coarse word (for devices without an exact percent).
_LEVEL_NAMES: dict[int, str] = {
    3: _("Low"),
    4: _("Critical"),
    6: _("Normal"),
    7: _("High"),
    8: _("Full"),
}


class PeripheralBattery(NamedTuple):
    """Charge info for one wireless accessory."""

    model: str
    kind: str  # Human-readable device type, e.g. "Keyboard"
    percent: float | None
    charging: bool
    level: str  # Coarse level word when no exact percent, else ""


def _peripheral_from_props(props: dict[str, Any]) -> PeripheralBattery | None:
    """Build a PeripheralBattery from UPower device properties, or None."""
    if props.get("PowerSupply", True):
        return None
    device_type = int(props.get("Type", 0))
    if device_type in _EXCLUDED_TYPES:
        return None

    percent = props.get("Percentage")
    state = int(props.get("State", 0))
    return PeripheralBattery(
        model=str(props.get("Model", "") or ""),
        kind=_TYPE_NAMES.get(device_type, _("Device")),
        percent=float(percent) if percent is not None else None,
        charging=state == _STATE_CHARGING,
        level=_LEVEL_NAMES.get(int(props.get("BatteryLevel", 0)), ""),
    )


def peripheral_label(peripheral: PeripheralBattery) -> str:
    """Menu/tooltip row text, e.g. ``Keychron K8: 96%``."""
    name = peripheral.model or peripheral.kind
    charge = _charge_text(peripheral=peripheral)
    return f"{name}: {charge}" if charge else name


def tooltip_lines(
    *, battery_line: str, peripherals: Iterable[PeripheralBattery]
) -> str:
    """Compose the battery tooltip with one peripheral row per line beneath it."""
    lines = [battery_line]
    lines.extend(peripheral_label(peripheral=peripheral) for peripheral in peripherals)
    return "\n".join(lines)


def _charge_text(*, peripheral: PeripheralBattery) -> str:
    if peripheral.percent is not None and peripheral.percent > 0:
        text = f"{peripheral.percent:.0f}%"
    elif peripheral.level:
        text = peripheral.level
    else:
        return ""
    if peripheral.charging:
        text = _("{charge} (charging)").format(charge=text)
    return text


def read_peripheral_batteries() -> list[PeripheralBattery]:
    """Enumerate accessory batteries via UPower, sorted lowest charge first."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        paths = bus.call_sync(
            _UPOWER,
            _UPOWER_PATH,
            _UPOWER,
            "EnumerateDevices",
            None,
            GLib.VariantType("(ao)"),
            Gio.DBusCallFlags.NONE,
            _DBUS_TIMEOUT_MS,
            None,
        ).unpack()[0]
    except GLib.Error as exc:
        log.debug("UPower enumerate failed: %s", exc)
        return []

    peripherals: list[PeripheralBattery] = []
    for path in paths:
        try:
            props = bus.call_sync(
                _UPOWER,
                path,
                "org.freedesktop.DBus.Properties",
                "GetAll",
                GLib.Variant("(s)", (_UPOWER_DEVICE,)),
                GLib.VariantType("(a{sv})"),
                Gio.DBusCallFlags.NONE,
                _DBUS_TIMEOUT_MS,
                None,
            ).unpack()[0]
        except GLib.Error as exc:
            log.debug("UPower properties failed for %s: %s", path, exc)
            continue
        peripheral = _peripheral_from_props(props=props)
        if peripheral is not None:
            peripherals.append(peripheral)

    peripherals.sort(key=lambda p: p.percent if p.percent is not None else 999.0)
    return peripherals
