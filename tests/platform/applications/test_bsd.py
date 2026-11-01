"""BSD integration regression tests at their owning boundaries."""

from types import SimpleNamespace

import pytest

from docking.platform import environment
from docking.platform.applications.identity import (
    LaunchProvenanceStore,
)
from docking.platform.applications.implementations.bsd import process as bsd
from tests.platform_factories import make_process_identity


def test_process_disappearing_does_not_abort_tracking(monkeypatch):
    def missing():
        raise ProcessLookupError("gone")

    monkeypatch.setattr(bsd, "_psutil", missing)
    assert bsd.process_executable(42) is None
    assert bsd.process_script(42) is None


def test_openbsd_does_not_guess_process_executable_from_its_own_path(monkeypatch):
    monkeypatch.setattr(bsd.sys, "platform", "openbsd7")
    process = SimpleNamespace(cmdline=lambda: ["editor"], exe=lambda: "/wrong/editor")
    monkeypatch.setattr(
        bsd, "_psutil", lambda: SimpleNamespace(Process=lambda pid: process)
    )
    assert bsd.process_executable(42) is None


def test_identity_uses_native_process_readers_without_proc(monkeypatch, tmp_path):
    executable = tmp_path / "program"
    executable.touch()
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    monkeypatch.setattr(bsd, "process_executable", lambda pid: executable)
    monkeypatch.setattr(bsd, "process_script", lambda pid: "program.py")
    result = make_process_identity(
        LaunchProvenanceStore(), backend=bsd.BsdProcessBackend()
    ).identity_for_pid(42)
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
