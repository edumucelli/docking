"""Actual D-Bus, UnixFDList, pipe, timeout and main-loop service contracts."""

import json
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("dbus-run-session") is None, reason="Needs dbus-run-session"
)


def run_private(mode):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY"}
    }
    result = subprocess.run(
        [
            "dbus-run-session",
            "--",
            sys.executable,
            "-m",
            "tests.bdd_support.native_bus_worker",
            mode,
        ],
        env=environment,
        text=True,
        capture_output=True,
        timeout=8,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize(
    "mode", ["valid", "truncated", "wrong-window", "oversized", "denied", "timeout"]
)
def test_screenshot_contract(mode):
    result = run_private(mode)
    assert result["preview"] is (mode == "valid")
    if mode == "timeout":
        assert result["ticks"] > 10, (
            "GTK main context must stay responsive during capture"
        )


@pytest.mark.parametrize("mode,expected", [("idle", 12.5), ("idle-failed", None)])
def test_mutter_idle_contract(mode, expected):
    assert run_private(mode)["idle"] == expected
