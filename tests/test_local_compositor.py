"""Local harness compatibility across old and new compositor runtimes."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("no_xwayland", [False, True])
def test_phoc_adapter_accepts_old_and_new_command_line_options(tmp_path, no_xwayland):
    command = tmp_path / "phoc"
    command.write_text(
        f"#!/bin/sh\necho '{'--no-xwayland' if no_xwayland else '--config'}'\n"
    )
    command.chmod(0o755)
    completed = subprocess.run(
        [
            "bash",
            "-c",
            'set -eu; source "$1"; adapter_prepare; printf "%s" "${PHOC_LAB_ARGS[*]}"',
            "test",
            str(ROOT / "tools/visual_compositor/adapters/phoc.sh"),
        ],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "LAB_DIR": str(tmp_path),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout == ("--no-xwayland" if no_xwayland else "")
    assert "xwayland=false" in (tmp_path / "config/phoc.ini").read_text()


@pytest.mark.parametrize("nested", [False, True])
def test_gnome_adapter_preserves_nested_and_supports_headless(tmp_path, nested):
    command = tmp_path / "gnome-shell"
    command.write_text(f"#!/bin/sh\necho '{'--nested' if nested else '--headless'}'\n")
    command.chmod(0o755)
    completed = subprocess.run(
        [
            "bash",
            "-c",
            (
                'set -eu; source "$1"; adapter_prepare; '
                'printf "%s|%s" "$GNOME_LAB_MODE" "$LAB_CAPTURE_MODE"'
            ),
            "test",
            str(ROOT / "tools/visual_compositor/adapters/gnome.sh"),
        ],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "LAB_DIR": str(tmp_path),
            "LAB_CAPTURE_MODE": "x11",
            "DBUS_SESSION_BUS_ADDRESS": "private-test-bus",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout == ("nested|x11" if nested else "headless|gnome")


def test_virtual_pointer_binding_imports_with_supported_pywayland():
    pytest.importorskip("pywayland")
    from tools.visual_compositor.probes.generated.wlr_virtual_pointer_unstable_v1 import (
        ZwlrVirtualPointerManagerV1,
    )

    assert ZwlrVirtualPointerManagerV1.version == 2
    request = next(
        message
        for message in ZwlrVirtualPointerManagerV1.requests
        if message.name == "create_virtual_pointer"
    )
    assert request.arguments[0].interface.name == "wl_seat"


def test_local_runner_rejects_unknown_mode_before_starting_a_compositor():
    result = subprocess.run(
        ["bash", str(ROOT / "tools/visual_compositor/local_run.sh"), "unknown", "sway"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Unknown local mode" in result.stderr
