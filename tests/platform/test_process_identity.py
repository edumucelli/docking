"""Tests for runtime process identity and launch provenance."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

from docking.platform.applications.identity import (
    LaunchProvenanceStore,
    ProcessIdentityService,
)


def _service() -> ProcessIdentityService:
    return ProcessIdentityService(LaunchProvenanceStore())


def test_identity_reads_current_process_executable():
    identity = _service().identity_for_pid(os.getpid())

    assert identity is not None
    assert identity.executable_path == Path("/proc/self/exe").resolve()


def test_recorded_launch_is_bound_to_exact_process_pid(tmp_path):
    executable = tmp_path / "launcher"
    executable.write_bytes(b"\x7fELF")
    process = SimpleNamespace(pid=os.getpid(), poll=lambda: None)
    service = _service()

    service.provenance_store.record_launch(
        process=process,
        desktop_id="tool.desktop",
        executable_path=executable.resolve(),
    )
    identity = service.identity_for_pid(os.getpid())

    assert identity is not None
    assert identity.launch is not None
    assert identity.launch.desktop_id == "tool.desktop"
    assert identity.launch.executable_path == executable.resolve()


def test_finished_launch_record_is_discarded():
    process = SimpleNamespace(pid=os.getpid(), poll=lambda: 0)
    service = _service()
    service.provenance_store.record_launch(
        process=process,
        desktop_id="tool.desktop",
        executable_path=None,
    )

    identity = service.identity_for_pid(os.getpid())

    assert identity is not None
    assert identity.launch is None
