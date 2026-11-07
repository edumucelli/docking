"""NetBSD envsys sensors retain units, validity, and unknown battery times."""

import plistlib

import pytest

from docking.applets.battery.implementations.bsd import backend as battery_bsd
from docking.applets.thermals.implementations.bsd import backend as thermals_bsd
from docking.platform.host.implementations.bsd import envsys


def sensor(name, unit, value, **extra):
    return {
        "description": name,
        "type": unit,
        "cur-value": value,
        "state": "valid",
        **extra,
    }


@pytest.fixture
def readings(monkeypatch):
    devices = {
        "acpiacad0": [sensor("connected", "Indicator", 0)],
        "acpibat0": [
            sensor("present", "Indicator", 1),
            sensor("charge", "Watt hour", 30_000_000),
            sensor("last full cap", "Watt hour", 40_000_000),
            sensor("charging", "Battery charge", 0),
            sensor("discharge rate", "Watts", 10_000_000),
        ],
        "acpitz0": [sensor("temperature", "Temperature", 320_150_000)],
    }

    def command(argv):
        assert argv == ["envstat", "-x"]
        return plistlib.dumps(devices).decode()

    monkeypatch.setattr(envsys, "read_command", command)
    monkeypatch.setattr(battery_bsd.sys, "platform", "netbsd11")
    return devices


def test_battery_and_temperature_share_native_typed_readings(readings):
    assert battery_bsd.battery() == battery_bsd.Battery(75, "Discharging", 10800)
    assert thermals_bsd.temperatures()[0][0] == "acpitz0: temperature"
    assert thermals_bsd.temperatures()[0][1] == pytest.approx(47)


@pytest.mark.parametrize(
    "unit,rate_unit", [("Watt hour", "Watts"), ("Ampere hour", "Ampere")]
)
def test_charging_time_uses_remaining_capacity_in_matching_units(
    readings, unit, rate_unit
):
    readings["acpibat0"] = [
        sensor("present", "Indicator", 1),
        sensor("charge", unit, 30_000_000, **{"max-value": 40_000_000}),
        sensor("charging", "Battery charge", 1),
        sensor("charge rate", rate_unit, 10_000_000),
    ]
    readings["acpiacad0"][0]["cur-value"] = 1
    assert battery_bsd.battery() == battery_bsd.Battery(75, "Charging", 3600)


@pytest.mark.parametrize("rate", [0, -1, None])
def test_missing_or_invalid_discharge_rate_keeps_unknown_time(readings, rate):
    readings["acpibat0"][-1]["cur-value"] = rate
    if rate is None:
        readings["acpibat0"][-1]["state"] = "invalid"
        del readings["acpibat0"][-1]["cur-value"]
    assert battery_bsd.battery() == battery_bsd.Battery(75, "Discharging", None)


def test_plugged_battery_without_charging_is_full(readings):
    readings["acpiacad0"][0]["cur-value"] = 1
    assert battery_bsd.battery() == battery_bsd.Battery(75, "Full", None)


@pytest.mark.parametrize(
    "field,value", [("present", 0), ("last full cap", 0), ("charge", -1)]
)
def test_absent_or_unusable_battery_has_no_reading(readings, field, value):
    next(s for s in readings["acpibat0"] if s["description"] == field)["cur-value"] = (
        value
    )
    assert battery_bsd.battery() is None


def test_invalid_temperature_is_ignored_but_critical_reading_is_kept(readings):
    readings["acpitz0"] = [
        sensor("invalid", "Temperature", 0, state="invalid"),
        sensor("critical", "Temperature", 373_150_000, state="critical-over"),
        sensor("voltage", "Voltage DC", 12_000_000),
    ]
    assert thermals_bsd.temperatures() == [("acpitz0: critical", 100)]


def test_proplib_hexadecimal_integer_and_size_attribute():
    output = """<?xml version="1.0"?>
<!DOCTYPE plist PUBLIC "-//NetBSD//DTD PLIST 1.0//EN" "http://www.NetBSD.org/XML/prop-list.dtd">
<plist version="1.0"><dict><key>cpu0</key><array><dict>
<key>description</key><string>temperature</string>
<key>type</key><string>Temperature</string>
<key>state</key><string>valid</string>
<key>cur-value</key><integer size="64">0x131519f0</integer>
</dict></array></dict></plist>"""
    assert envsys.parse_sensors(output) == (
        envsys.Sensor("cpu0", "temperature", "Temperature", 320_150_000, None),
    )


@pytest.mark.parametrize(
    "output", ["", "not XML", "<plist><dict>", "<plist><array/></plist>"]
)
def test_invalid_property_list_is_unavailable(output):
    assert envsys.parse_sensors(output) == ()


def test_failed_sensor_command_is_unavailable(monkeypatch):
    monkeypatch.setattr(envsys, "read_command", lambda _argv: None)
    assert envsys.read_sensors() == ()
