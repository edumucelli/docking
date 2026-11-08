"""Local harness compatibility across old and new compositor runtimes."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "script,args",
    [
        ("native_services.sh", ["phoc", "foreign"]),
        ("wayfire_services.sh", []),
        ("cinnamon_services.sh", []),
    ],
)
def test_service_runner_creates_evidence_in_fresh_checkout(tmp_path, script, args):
    scripts = tmp_path / "checkout/tools/linux/visual"
    scripts.mkdir(parents=True)
    destination = scripts / script
    shutil.copy2(ROOT / "tools/linux/visual" / script, destination)
    commands = tmp_path / "commands"
    commands.mkdir()
    docker = commands / "docker"
    docker.write_text('#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$DOCKER_CALL_LOG"\n')
    docker.chmod(0o755)
    calls = tmp_path / "docker-calls.txt"

    result = subprocess.run(
        ["bash", str(destination), *args],
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "DOCKER_CALL_LOG": str(calls),
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert (scripts / "evidence").is_dir()
    docker_calls = calls.read_text().splitlines()
    assert len(docker_calls) == 3
    assert f"{tmp_path / 'checkout'}:/src:ro" in docker_calls[-1]
    assert f"/src/tools/linux/visual/{script} --session" in docker_calls[-1]


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
            str(ROOT / "tools/linux/visual/adapters/phoc.sh"),
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
            str(ROOT / "tools/linux/visual/adapters/gnome.sh"),
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
    from tools.linux.visual.probes.generated.wlr_virtual_pointer_unstable_v1 import (
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
        ["bash", str(ROOT / "tools/linux/visual/local_run.sh"), "unknown", "sway"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Unknown local mode" in result.stderr


@pytest.mark.parametrize("compositor", ["cinnamon", "sway"])
def test_stack_capture_uses_the_compositor_screenshot_backend(
    monkeypatch, tmp_path, compositor
):
    import importlib
    from unittest.mock import MagicMock

    monkeypatch.syspath_prepend(str(ROOT / "tools/linux/visual/probes"))
    module = importlib.import_module("stack_action")
    run = MagicMock()
    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setenv("COMPOSITOR", compositor)
    monkeypatch.setenv("WAYLAND_DISPLAY", "child-wayland")
    monkeypatch.setenv("LAB_PARENT_DISPLAY", "parent-wayland")
    target = tmp_path / "stack.png"

    module.capture_screen(target, 2)

    command = run.call_args.args[0]
    if compositor == "cinnamon":
        assert Path(command[1]).name == "cinnamon_probe.py"
        assert command[2:] == ["screenshot", str(target)]
    else:
        assert command == ["grim", "-s", "2", str(target)]
    assert run.call_args.kwargs["env"]["WAYLAND_DISPLAY"] == "parent-wayland"


@pytest.mark.parametrize("has_workarea", [True, False])
def test_autohide_stimulus_uses_the_usable_edge(tmp_path, has_workarea):
    import json
    import shutil

    if shutil.which("jq") is None:
        pytest.skip("jq is provided by the compositor lab images")

    output = {"x": 0, "y": 0, "width": 1280, "height": 720}
    if has_workarea:
        output["workarea"] = {**output, "height": 680}
    calls = tmp_path / "pointer-calls"
    completed = subprocess.run(
        [
            "bash",
            "-c",
            "\n".join(
                [
                    "set -eu",
                    'source "$1"',
                    'adapter_geometry() { printf "%s" "$OUTPUT_GEOMETRY"; }',
                    'lab_pointer() { printf "%s %s\\n" "$1" "$2" >> "$POINTER_CALLS"; }',
                    "sleep() { :; }",
                    "capture_phase() { :; }",
                    "run_lab_action autohide bottom",
                ]
            ),
            "test",
            str(ROOT / "tools/linux/visual/actions.sh"),
        ],
        env={
            **os.environ,
            "OUTPUT_GEOMETRY": json.dumps({"outputs": [output]}),
            "POINTER_CALLS": str(calls),
        },
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    points = [tuple(map(int, line.split())) for line in calls.read_text().splitlines()]
    edge = 679 if has_workarea else 719
    center = 340 if has_workarea else 360
    assert points == [
        (640, center),
        (640, edge),
        (640, center),
        (640, edge),
        (640, center),
        (640, edge),
    ]


def test_cinnamon_capture_waits_for_a_complete_png(monkeypatch, tmp_path):
    import importlib
    import io
    from types import SimpleNamespace

    pytest.importorskip("gi")
    from PIL import Image

    monkeypatch.syspath_prepend(str(ROOT / "tools/linux/visual/probes"))
    module = importlib.import_module("cinnamon_probe")
    encoded = io.BytesIO()
    Image.new("RGB", (20, 20), "red").save(encoded, format="PNG")
    data = encoded.getvalue()
    path = tmp_path / "capture.png"
    proxy = SimpleNamespace(call_sync=lambda *_args: path.write_bytes(data[:20]))
    monkeypatch.setattr(module, "_proxy", lambda: proxy)
    monkeypatch.setattr(module, "_eval", lambda *_args: None)
    monkeypatch.setattr(module.time, "sleep", lambda _delay: path.write_bytes(data))

    assert module.screenshot(str(path)) == 0
    assert path.read_bytes() == data
