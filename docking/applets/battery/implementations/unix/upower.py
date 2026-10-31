"""UPower peripheral acquisition."""

from __future__ import annotations

import shutil
import subprocess

import gi

from ...state import _parse_upower_duration_seconds

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib

from ...peripherals import (
    _DBUS_TIMEOUT_MS,
    _UPOWER,
    _UPOWER_DEVICE,
    _UPOWER_PATH,
    PeripheralBattery,
    _peripheral_from_props,
    log,
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


def _upower_seconds_remaining(*, bat_name: str, status: str) -> int | None:
    if shutil.which("upower") is None:
        return None
    normalized = status.lower().strip()
    if normalized not in {"charging", "discharging"}:
        return None
    device = f"/org/freedesktop/UPower/devices/battery_{bat_name}"
    try:
        proc = subprocess.run(
            ["upower", "-i", device],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("Failed to query upower for %s: %s", bat_name, exc)
        return None
    if proc.returncode != 0:
        return None
    key = "time to full" if normalized == "charging" else "time to empty"
    return _parse_upower_duration_seconds(text=proc.stdout, key=key)
