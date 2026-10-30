"""Discover real desktop entries and honor their launch context."""

import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import docking.platform.applications.desktop_launch as launcher_mod
from docking.platform.applications.launcher import ApplicationLauncher
from docking.platform.applications.registry import ApplicationRegistry


def _discover(tmp_path, *, extra, gio):
    path = tmp_path / "audit-launch.desktop"
    path.write_text(
        "[Desktop Entry]\nType=Application\nName=Audit Launch\n"
        f"Exec=/bin/true --fixture\n{extra}\n"
    )
    registry = ApplicationRegistry(
        application_source=lambda: (), desktop_directories_source=lambda: (tmp_path,)
    )
    if not gio:
        registry._desktop_app_info_for_id = lambda _id: None
        registry._desktop_app_info_from_filename = lambda _path: None
    registry.refresh()
    assert registry.get(path.name) is not None
    return registry


@pytest.mark.parametrize("gio", [False, True])
def test_terminal_desktop_entry_launches_in_terminal(tmp_path, monkeypatch, gio):
    registry = _discover(tmp_path, extra="Terminal=true", gio=gio)
    terminal = MagicMock(
        return_value=["/usr/bin/fake-terminal", "-e", "/bin/true", "--fixture"]
    )
    monkeypatch.setattr(launcher_mod.commands, "build_terminal_argv", terminal)
    popen = MagicMock()
    launcher = ApplicationLauncher(registry, MagicMock(), popen=popen)

    assert launcher.launch("audit-launch.desktop")
    terminal.assert_called_once()
    assert popen.call_args.args[0][0] == "/usr/bin/fake-terminal"


@pytest.mark.parametrize("gio", [False, True])
def test_desktop_entry_sets_declared_working_directory(tmp_path, gio):
    folder = tmp_path / "working directory"
    folder.mkdir()
    registry = _discover(tmp_path, extra=f"Path={folder}", gio=gio)
    popen = MagicMock()
    launcher = ApplicationLauncher(registry, MagicMock(), popen=popen)

    assert launcher.launch("audit-launch.desktop")
    assert Path(popen.call_args.kwargs["cwd"]) == folder


def test_real_child_process_uses_desktop_working_directory(tmp_path):
    folder = tmp_path / "working directory"
    folder.mkdir()
    output = tmp_path / "observed-directory.txt"
    script = tmp_path / "record_directory.py"
    script.write_text(
        "import pathlib,sys\npathlib.Path(sys.argv[1]).write_text(str(pathlib.Path.cwd()))\n"
    )
    registry = _discover(tmp_path, extra=f"Path={folder}", gio=False)
    desktop_file = tmp_path / "audit-launch.desktop"
    desktop_file.write_text(
        "[Desktop Entry]\nType=Application\nName=Audit Launch\n"
        f"Exec={shlex.join([sys.executable, str(script), str(output)])}\nPath={folder}\n"
    )
    registry.refresh()
    processes = []

    def spawn(*args, **kwargs):
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    launcher = ApplicationLauncher(registry, MagicMock(), popen=spawn)
    assert launcher.launch("audit-launch.desktop")
    assert processes[0].wait(timeout=5) == 0
    assert output.read_text() == str(folder)


def test_missing_terminal_does_not_launch_terminal_app_invisibly(tmp_path, monkeypatch):
    registry = _discover(tmp_path, extra="Terminal=true", gio=False)
    monkeypatch.setattr(
        launcher_mod.commands, "build_terminal_argv", lambda **_kwargs: None
    )
    popen = MagicMock()
    launcher = ApplicationLauncher(registry, MagicMock(), popen=popen)
    assert not launcher.launch("audit-launch.desktop")
    popen.assert_not_called()


def test_host_working_directory_is_sent_to_flatpak_spawn(tmp_path, monkeypatch):
    from docking.platform.applications.types import ApplicationLocation
    from tests.platform.applications.test_application_launcher import (
        _application,
        _launcher,
    )

    application = replace(
        _application(location=ApplicationLocation.HOST),
        working_directory="/host/working directory",
    )
    popen = MagicMock()
    launcher, _registry, _store = _launcher(application, popen=popen)
    monkeypatch.setattr(
        launcher_mod.flatpak, "spawn_path", lambda **_kwargs: "/usr/bin/flatpak-spawn"
    )

    assert launcher.launch(application.desktop_id)
    argv = popen.call_args.args[0]
    assert argv[:4] == [
        "/usr/bin/flatpak-spawn",
        "--host",
        "--directory=/host/working directory",
        "env",
    ]
    assert popen.call_args.kwargs["cwd"] is None
