"""UPower peripheral discovery used by the Linux and BSD backends."""

import re
from typing import Any

from gi.repository import Gio, GLib

from docking.applets.battery.peripherals import PeripheralBattery
from docking.i18n import _
from docking.log import get_logger

log = get_logger("battery.upower")

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


def _parse_upower_duration_seconds(*, text: str, key: str) -> int | None:
    pattern = rf"^\s*{re.escape(key)}:\s*(.+?)\s*$"
    for line in text.splitlines():
        match = re.match(pattern, line, re.IGNORECASE)
        if not match:
            continue
        return _parse_duration_seconds(raw=match.group(1))
    return None


def _parse_duration_seconds(*, raw: str) -> int | None:
    lowered = raw.strip().lower()
    if not lowered or lowered in {"n/a", "unknown", "0"}:
        return None

    total = 0.0
    matched = False
    pattern = r"([0-9]+(?:\.[0-9]+)?)\s*(hours?|hrs?|minutes?|mins?|seconds?|secs?)"
    for value, unit in re.findall(pattern, lowered):
        matched = True
        amount = float(value)
        if unit.startswith(("hour", "hr")):
            total += amount * 3600
        elif unit.startswith(("minute", "min")):
            total += amount * 60
        else:
            total += amount
    if not matched or total <= 0:
        return None
    return max(1, int(total))
