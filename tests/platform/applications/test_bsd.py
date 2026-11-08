"""BSD integration regression tests at their owning boundaries."""

import sys
from types import SimpleNamespace

import pytest

from docking.platform import environment
from docking.platform.applications.identity import (
    LaunchProvenanceStore,
)
from docking.platform.applications.implementations.bsd import netbsd
from docking.platform.applications.implementations.bsd import process as bsd
from tests.platform_factories import make_process_identity


def test_process_disappearing_does_not_abort_tracking(monkeypatch):
    def missing():
        raise ProcessLookupError("gone")

    monkeypatch.setattr(bsd, "_psutil", missing)
    assert bsd.process_executable(42) is None
    assert bsd.process_script(42) is None


def test_openbsd_does_not_guess_process_executable_from_its_own_path(monkeypatch):
    monkeypatch.setattr(sys, "platform", "openbsd7")
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
    assert result.process_title is None


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


@pytest.mark.parametrize(
    "system,kernel_path",
    [
        ("freebsd15", True),
        ("openbsd7", False),
        ("netbsd11", True),
    ],
)
def test_native_executable_evidence_uses_the_supported_source(
    monkeypatch, tmp_path, system, kernel_path
):
    monkeypatch.setattr(sys, "platform", system)
    executable = tmp_path / "kernel-program"
    executable.touch()
    command = tmp_path / "argv-program"
    command.touch()
    process = SimpleNamespace(
        pid=42, exe=lambda: str(executable), cmdline=lambda: [str(command)]
    )
    monkeypatch.setattr(
        bsd, "_psutil", lambda: SimpleNamespace(Process=lambda pid: process)
    )
    monkeypatch.setattr(netbsd, "_pathname", lambda pid: str(executable))
    assert bsd.process_executable(42) == (executable if kernel_path else command)


def test_netbsd_kernel_path_does_not_call_procfs_exe(monkeypatch, tmp_path):
    import ctypes

    executable = tmp_path / "native-program"
    executable.touch()
    data = bytes(executable) + b"\0"
    requests = []

    def sysctl(mib, count, buffer, size, new_value, new_size):
        requests.append(tuple(mib))
        assert count == 4 and tuple(mib) == (1, 48, 42, 5)
        assert new_value is None and new_size == 0
        length = ctypes.cast(size, ctypes.POINTER(ctypes.c_size_t))
        if buffer is not None:
            assert length.contents.value == len(data)
            ctypes.memmove(buffer, data, len(data))
        length.contents.value = len(data)
        return 0

    monkeypatch.setattr(netbsd, "_libc", lambda: SimpleNamespace(sysctl=sysctl))
    process = SimpleNamespace(pid=42, exe=lambda: pytest.fail("procfs exe used"))
    assert netbsd.executable_path(process) == str(executable)
    assert len(requests) == 2


@pytest.mark.parametrize("error", [2, 13])
def test_netbsd_executable_disappearance_or_denial_is_unavailable(monkeypatch, error):
    import ctypes

    def sysctl(*args):
        ctypes.set_errno(error)
        return -1

    monkeypatch.setattr(sys, "platform", "netbsd11")
    monkeypatch.setattr(netbsd, "_libc", lambda: SimpleNamespace(sysctl=sysctl))
    monkeypatch.setattr(
        bsd,
        "_psutil",
        lambda: SimpleNamespace(Process=lambda pid: SimpleNamespace(pid=pid)),
    )
    assert bsd.process_executable(42) is None
