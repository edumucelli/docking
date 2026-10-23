"""BSD integration regression tests at their owning boundaries."""

import pytest

from docking.applets.session import bsd


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
