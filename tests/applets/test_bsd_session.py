"""BSD integration regression tests at their owning boundaries."""

import sys

import pytest

from docking.applets.session.implementations.bsd import backend as bsd


@pytest.mark.parametrize(
    "system,original,expected",
    [
        ("openbsd7", ["systemctl", "suspend"], ["zzz"]),
        ("freebsd15", ["systemctl", "suspend"], ["acpiconf", "-s", "3"]),
        ("openbsd7", ["systemctl", "poweroff"], ["shutdown", "-hp", "now"]),
        ("freebsd15", ["systemctl", "poweroff"], ["shutdown", "-p", "now"]),
        (
            "netbsd11",
            ["systemctl", "suspend"],
            ["sysctl", "-w", "hw.acpi.sleep.state=3"],
        ),
        ("netbsd11", ["systemctl", "poweroff"], ["shutdown", "-p", "now"]),
        ("netbsd11", ["systemctl", "reboot"], ["shutdown", "-r", "now"]),
    ],
)
def test_session_actions_select_native_commands_without_elevation(
    monkeypatch, system, original, expected
):
    monkeypatch.setattr(sys, "platform", system)
    monkeypatch.setattr(bsd.shutil, "which", lambda command: f"/sbin/{command}")
    assert bsd.command_for(original[-1]) == expected


def test_unavailable_native_commands_return_none(monkeypatch):
    monkeypatch.setattr(bsd.shutil, "which", lambda command: None)
    assert bsd.command_for("reboot") is None
