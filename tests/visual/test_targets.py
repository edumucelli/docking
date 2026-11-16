"""Target selection must cover real desktops without launching them during pytest."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from types import SimpleNamespace

import pytest

from tests.visual import __main__ as runner
from tests.visual.targets import ROOT, TARGETS, target_named
from tools.linux import visual_vm


def test_registry_covers_the_agreed_desktops_without_duplicate_targets():
    names = [target.name for target in TARGETS]
    assert len(names) == len(set(names))
    assert set(names) == {
        "sway",
        "cinnamon",
        "niri",
        "labwc",
        "cosmic",
        "kwin",
        "wayfire",
        "hyprland",
        "treeland",
        "jay",
        "miriway",
        "gamescope",
        "phoc",
        "phosh",
        "weston",
        "cage",
        "gnome",
        "gnome-fallback",
        *(
            f"x11-{desktop}"
            for desktop in (
                "gnome",
                "plasma",
                "cinnamon",
                "xfce",
                "mate",
                "lxde",
                "lxqt",
            )
        ),
        "freebsd-xfce",
        "openbsd-xfce",
        "netbsd-xfce",
        "windows-server",
        "windows-11",
    }


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.name)
def test_targets_have_their_backend_module_and_pinned_build_target(target):
    assert target.directory.is_dir()
    if target.system != "linux":
        return
    dockerfile = runner.LINUX / target.image_file
    stages = re.findall(r"^FROM .+ AS (\S+)$", dockerfile.read_text(), re.MULTILINE)
    assert target.image_target in stages
    if target.services != "x11":
        assert (target.directory / "adapter.sh").is_file()


def test_listing_targets_needs_no_docker_or_native_desktop(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["visual", "--list"])
    monkeypatch.setattr(
        runner.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("Listing launched a process"),
    )
    assert runner.main() == 0
    assert {row["name"] for row in json.loads(capsys.readouterr().out)} == {
        target.name for target in TARGETS
    }


def test_wrong_host_is_rejected_before_starting_a_runner(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        sys,
        "argv",
        ["visual", "--target", "windows-11", "--output", str(tmp_path / "run")],
    )
    with pytest.raises(SystemExit) as error:
        runner.main()
    assert error.value.code == 2
    assert not (tmp_path / "run").exists()


def test_native_failure_is_retained_and_cannot_become_a_success(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        sys, "argv", ["visual", "--target", "sway", "--output", str(tmp_path / "run")]
    )
    monkeypatch.setattr(
        runner, "commands", lambda *_: [["failed-probe"], ["later-probe"]]
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=17 if command == ["failed-probe"] else 0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.main() == 17
    report = json.loads((tmp_path / "run/target.json").read_text())
    assert report["results"][0]["status"] == "fail"
    assert report["results"][0]["exit_code"] == 17
    assert ["later-probe"] not in calls


@pytest.mark.parametrize("name", ["cage", "weston", "gnome-fallback"])
def test_reduced_targets_require_visible_launcher_input(name, tmp_path):
    commands = runner.commands(target_named(name), tmp_path, None)
    assert len(commands) == 1
    assert commands[0][-1] == "launcher"


def test_windows_minimum_preserves_installed_runtime_and_all_four_edges(tmp_path):
    installed = tmp_path / "installed"
    commands = runner.commands(target_named("windows-11"), tmp_path, installed)
    placement = [command for command in commands if "--placement-only" in command]
    assert {command[command.index("--position") + 1] for command in placement} == {
        "bottom",
        "top",
        "left",
        "right",
    }
    assert all(
        command[command.index("--installed-root") + 1] == str(installed)
        for command in placement
    )
    assert any(
        "--multiple-windows" in command and "--uncataloged" in command
        for command in commands
    )


def test_guest_checksum_rejects_partial_or_changed_downloads(tmp_path, monkeypatch):
    image = tmp_path / "guest.qcow2"
    image.write_bytes(b"owned fixture image")
    monkeypatch.setattr(
        visual_vm, "IMAGE_SHA512", hashlib.sha512(image.read_bytes()).hexdigest()
    )
    assert visual_vm.verify_image(image)
    image.write_bytes(b"truncated")
    assert not visual_vm.verify_image(image)


def test_guest_uses_an_emulated_gpu_and_loopback_only_ssh(tmp_path):
    command = visual_vm.qemu_command(tmp_path / "disk", tmp_path / "seed", 12345)
    assert "virtio-gpu-pci" in command
    assert "user,id=net0,hostfwd=tcp:127.0.0.1:12345-:22" in command
    assert not any("vfio" in arg or "/dev/dri" in arg for arg in command)


def test_visual_source_has_no_superseded_platform_directories():
    for directory in (
        ROOT / "tools/linux/visual",
        ROOT / "tools/bsd/visual",
        ROOT / "tools/windows/visual",
    ):
        assert not any(
            path.suffix in {".py", ".sh", ".cpp", ".cs"}
            for path in directory.rglob("*")
            if "__pycache__" not in path.parts
        )
