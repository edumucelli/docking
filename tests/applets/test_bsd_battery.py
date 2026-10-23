"""BSD integration regression tests at their owning boundaries."""

from types import SimpleNamespace

import pytest

from docking.applets.battery import bsd


@pytest.mark.parametrize(
    "status,capacity,ac,expected",
    [
        ("3", "65", "1", "Charging"),
        ("0", "100", "1", "Full"),
        ("1", "8", "0", "Discharging"),
        ("4", "0", "0", None),
        ("255", "255", "255", None),
    ],
)
def test_openbsd_apm_state(monkeypatch, status, capacity, ac, expected):
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    values = {"-l": capacity, "-b": status, "-m": "42", "-a": ac}
    monkeypatch.setattr(bsd, "command", lambda argv: values[argv[-1]])
    reading = bsd.battery()
    if expected is None:
        assert reading is None
    else:
        assert reading == bsd.Battery(int(capacity), expected, 2520)


def test_freebsd_battery_unknown_time_stays_unknown(monkeypatch):
    monkeypatch.setattr(bsd.sys, "platform", "freebsd15")
    reading = SimpleNamespace(percent=75, power_plugged=False, secsleft=-1)
    monkeypatch.setattr(
        bsd, "_psutil", lambda: SimpleNamespace(sensors_battery=lambda: reading)
    )
    assert bsd.battery() == bsd.Battery(75, "Discharging", None)


@pytest.mark.parametrize("minutes", [None, "unknown", "-1"])
def test_openbsd_battery_unknown_time_preserves_reading(monkeypatch, minutes):
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    values = {"-l": "75", "-b": "0", "-m": minutes, "-a": "0"}
    monkeypatch.setattr(bsd, "command", lambda argv: values[argv[-1]])
    assert bsd.battery() == bsd.Battery(75, "Discharging", None)
