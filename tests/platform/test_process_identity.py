"""Tests for runtime process identity and launch provenance."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from docking.platform import process_identity
from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)


@pytest.fixture(autouse=True)
def _clear_launches():
    process_identity.clear_launches_for_tests()
    yield
    process_identity.clear_launches_for_tests()


def test_identity_reads_current_process_executable():
    identity = process_identity.identity_for_pid(os.getpid())

    assert identity is not None
    assert identity.executable_path == Path("/proc/self/exe").resolve()


def test_recorded_launch_is_bound_to_exact_process_pid(tmp_path):
    executable = tmp_path / "launcher"
    executable.write_bytes(b"\x7fELF")
    process = SimpleNamespace(pid=os.getpid(), poll=lambda: None)

    process_identity.record_launch(
        process=process,
        desktop_id="tool.desktop",
        executable_path=executable.resolve(),
    )
    identity = process_identity.identity_for_pid(os.getpid())

    assert identity is not None
    assert identity.launch is not None
    assert identity.launch.desktop_id == "tool.desktop"
    assert identity.launch.executable_path == executable.resolve()


def test_finished_launch_record_is_discarded():
    process = SimpleNamespace(pid=os.getpid(), poll=lambda: 0)
    process_identity.record_launch(
        process=process,
        desktop_id="tool.desktop",
        executable_path=None,
    )

    identity = process_identity.identity_for_pid(os.getpid())

    assert identity is not None
    assert identity.launch is None


def test_compatibility_facade_temporarily_uses_installed_service():
    shared_store = LaunchProvenanceStore()
    service = ProcessIdentityService(
        shared_store,
        executable_resolver=lambda _pid: Path("/opt/shared"),
    )
    previous = process_identity.configure_process_identity_service(service)
    try:
        process_identity.record_launch(
            process=SimpleNamespace(pid=99, poll=lambda: None),
            desktop_id="shared.desktop",
            executable_path=Path("/opt/shared"),
        )

        identity = process_identity.identity_for_pid(99)

        assert identity is not None
        assert identity.executable_path == Path("/opt/shared")
        assert identity.launch is not None
        assert identity.launch.desktop_id == "shared.desktop"
        assert service.provenance_store is shared_store
    finally:
        process_identity.reset_process_identity_service(previous)

    assert process_identity.get_process_identity_service() is previous
