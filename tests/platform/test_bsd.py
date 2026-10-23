"""BSD boundary behavior, with failures and data from native APIs represented."""

from __future__ import annotations

import socket
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking.platform import bsd
from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)


def test_cpu_uses_cumulative_native_times_without_sleeping(monkeypatch):
    times = namedtuple("Times", "user system idle nice irq")(2, 3, 10, 1, 1)
    monkeypatch.setattr(
        bsd, "_psutil", lambda: SimpleNamespace(cpu_times=lambda: times)
    )
    assert bsd.cpu_sample() == (17000, 10000)


def test_memory_percentage_is_normalized(monkeypatch):
    monkeypatch.setattr(
        bsd,
        "_psutil",
        lambda: SimpleNamespace(virtual_memory=lambda: SimpleNamespace(percent=42.5)),
    )
    assert bsd.memory_fraction() == 0.425


def test_process_disappearing_does_not_abort_tracking(monkeypatch):
    def missing():
        raise ProcessLookupError("gone")

    monkeypatch.setattr(bsd, "_psutil", missing)
    assert bsd.process_executable(42) is None
    assert bsd.process_script(42) is None


def test_identity_uses_native_process_readers_without_proc(monkeypatch, tmp_path):
    executable = tmp_path / "program"
    executable.touch()
    monkeypatch.setattr(bsd, "is_bsd", lambda: True)
    monkeypatch.setattr(bsd, "process_executable", lambda pid: executable)
    monkeypatch.setattr(bsd, "process_script", lambda pid: "program.py")
    result = ProcessIdentityService(LaunchProvenanceStore()).identity_for_pid(42)
    assert result is not None
    assert result.executable_path == executable
    assert result.script_basename == "program.py"


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["/usr/local/bin/python3.13", "/tmp/example.py", "private"], "example.py"),
        (["python3", "-c", "print('private')"], None),
        (["sh", "example.py"], None),
    ],
)
def test_script_identity_only_returns_python_script_basename(
    monkeypatch, argv, expected
):
    monkeypatch.setattr(
        bsd,
        "_psutil",
        lambda: SimpleNamespace(
            Process=lambda pid: SimpleNamespace(cmdline=lambda: argv)
        ),
    )
    assert bsd.process_script(42) == expected


def test_mount_table_keeps_zfs_and_deduplicates_mount_points(monkeypatch):
    partitions = [SimpleNamespace(mountpoint=p) for p in ("/", "/home", "/home")]
    monkeypatch.setattr(
        bsd,
        "_psutil",
        lambda: SimpleNamespace(
            disk_partitions=lambda **kwargs: partitions,
            disk_usage=lambda path: SimpleNamespace(percent=25),
        ),
    )
    assert bsd.mount_points() == (Path("/"), Path("/home"))
    assert bsd.disk_usage() == [("/", 0.25), ("/home", 0.25)]


def test_network_uses_native_counters_and_ignores_loopback(monkeypatch):
    counters = SimpleNamespace(bytes_recv=1000, bytes_sent=2000)
    util = SimpleNamespace(
        net_io_counters=lambda **kwargs: {"lo0": counters, "vio0": counters},
        net_if_addrs=lambda: {
            "vio0": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.5")]
        },
        net_if_stats=lambda: {
            "lo0": SimpleNamespace(isup=True),
            "vio0": SimpleNamespace(isup=True),
        },
    )
    monkeypatch.setattr(bsd, "_psutil", lambda: util)
    assert bsd.network_interfaces() == [
        bsd.NetworkInterface("vio0", "192.0.2.5", True, 1000, 2000)
    ]


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


def test_openbsd_sensors_ignore_voltages_and_fans(monkeypatch):
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    monkeypatch.setattr(
        bsd,
        "command",
        lambda argv: (
            "hw.sensors.cpu0.temp0=47.00 degC\nhw.sensors.acpitz0.temp0=45.00 degC\nhw.sensors.it0.fan0=800 RPM"
        ),
    )
    assert bsd.temperatures() == [
        ("hw.sensors.cpu0.temp0", 47.0),
        ("hw.sensors.acpitz0.temp0", 45.0),
    ]


@pytest.mark.parametrize(
    "system,original,expected",
    [
        ("openbsd7", ["systemctl", "suspend"], ["zzz"]),
        ("freebsd15", ["systemctl", "suspend"], ["acpiconf", "-s", "3"]),
        ("openbsd7", ["systemctl", "poweroff"], ["shutdown", "-hp", "now"]),
        ("freebsd15", ["systemctl", "poweroff"], ["shutdown", "-p", "now"]),
    ],
)
def test_session_actions_select_native_commands_without_elevation(
    monkeypatch, system, original, expected
):
    monkeypatch.setattr(bsd.sys, "platform", system)
    monkeypatch.setattr(bsd.shutil, "which", lambda command: f"/sbin/{command}")
    assert bsd.session_command(original) == expected


def test_unavailable_native_commands_return_none(monkeypatch):
    monkeypatch.setattr(bsd.shutil, "which", lambda command: None)
    assert bsd.session_command(["systemctl", "reboot"]) is None


def test_native_command_has_timeout_and_locale(monkeypatch):
    def run(argv, **kwargs):
        assert kwargs["timeout"] <= 0.5
        assert kwargs["env"]["LC_ALL"] == "C"
        return SimpleNamespace(returncode=0, stdout="42\n")

    monkeypatch.setattr(bsd.subprocess, "run", run)
    assert bsd.command(["sysctl", "-n", "hw.ncpu"]) == "42"
