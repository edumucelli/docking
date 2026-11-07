"""NetBSD ACPI battery values from the envsys sensor framework."""

from __future__ import annotations

from docking.platform.host.implementations.bsd.envsys import Sensor, read_sensors

from .backend import Battery


def battery() -> Battery | None:
    sensors = read_sensors()
    batteries: dict[str, dict[str, Sensor]] = {}
    connected = any(
        s.unit == "Indicator" and s.name == "connected" and s.value == 1
        for s in sensors
    )
    for sensor in sensors:
        if sensor.device.startswith("acpibat"):
            batteries.setdefault(sensor.device, {})[sensor.name] = sensor
    # The shared applet displays one system battery. Keep a stable choice when
    # several batteries are present instead of combining incompatible units.
    for device in sorted(batteries):
        readings = batteries[device]
        present, charge = readings.get("present"), readings.get("charge")
        if present is None or present.value != 1 or charge is None:
            continue
        full = readings.get("last full cap")
        maximum = (
            full.value
            if full is not None and full.unit == charge.unit
            else charge.maximum
        )
        if (
            charge.unit not in {"Watt hour", "Ampere hour"}
            or maximum is None
            or maximum <= 0
            or charge.value < 0
        ):
            continue
        capacity = max(0, min(100, round(charge.value * 100 / maximum)))
        charging = readings.get("charging")
        is_charging = charging is not None and charging.value == 1
        status = "Charging" if is_charging else "Full" if connected else "Discharging"
        remaining = None
        rate = readings.get("charge rate" if is_charging else "discharge rate")
        rate_unit = "Watts" if charge.unit == "Watt hour" else "Ampere"
        if rate is not None and rate.unit == rate_unit and rate.value > 0:
            amount = max(0, maximum - charge.value) if is_charging else charge.value
            if is_charging or not connected:
                remaining = round(amount * 3600 / rate.value)
        return Battery(capacity, status, remaining)
    return None
