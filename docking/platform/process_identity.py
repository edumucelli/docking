"""Compatibility facade for process identity and launch provenance."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any

from docking.platform.applications import identity as _identity
from docking.platform.applications.identity import (
    DEFAULT_MAX_LAUNCH_RECORDS,
    LaunchProvenance,
    LaunchProvenanceStore,
    ProcessIdentity,
    ProcessIdentityService,
)

_MAX_LAUNCH_RECORDS = DEFAULT_MAX_LAUNCH_RECORDS
_DEFAULT_PROVENANCE_STORE = LaunchProvenanceStore()
_DEFAULT_PROCESS_IDENTITY_SERVICE = ProcessIdentityService(
    _DEFAULT_PROVENANCE_STORE,
)
_service_lock = RLock()
_service = _DEFAULT_PROCESS_IDENTITY_SERVICE


def get_process_identity_service() -> ProcessIdentityService:
    """Return the compatibility service currently installed for this process."""
    with _service_lock:
        return _service


def configure_process_identity_service(
    service: ProcessIdentityService,
) -> ProcessIdentityService:
    """Install *service* and return the previous compatibility service."""
    global _service
    with _service_lock:
        previous = _service
        _service = service
        return previous


def reset_process_identity_service(
    previous: ProcessIdentityService | None = None,
) -> None:
    """Restore a previous service, or the module's standalone default."""
    global _service
    with _service_lock:
        _service = (
            previous if previous is not None else _DEFAULT_PROCESS_IDENTITY_SERVICE
        )


@contextmanager
def use_process_identity_service(
    service: ProcessIdentityService,
) -> Iterator[ProcessIdentityService]:
    """Temporarily install one service for legacy free-function consumers."""
    previous = configure_process_identity_service(service)
    try:
        yield service
    finally:
        reset_process_identity_service(previous)


def record_launch(
    *,
    process: subprocess.Popen[Any],
    desktop_id: str,
    executable_path: Path | None,
) -> None:
    """Record a launch in the currently installed shared store."""
    get_process_identity_service().provenance_store.record_launch(
        process=process,
        desktop_id=desktop_id,
        executable_path=executable_path,
    )


def identity_for_pid(pid: int | None) -> ProcessIdentity | None:
    """Delegate process lookup to the currently installed service."""
    return get_process_identity_service().identity_for_pid(pid)


def clear_launches_for_tests() -> None:
    """Clear records from the currently installed compatibility service."""
    get_process_identity_service().provenance_store.clear()


def _launch_provenance(pid: int) -> LaunchProvenance | None:
    return get_process_identity_service().provenance_store.provenance_for_pid(pid)


def _process_finished(process: subprocess.Popen[Any]) -> bool:
    return _identity._process_finished(process)


def _prune_finished_launches() -> None:
    get_process_identity_service().provenance_store.prune_finished()


def _process_executable_path(pid: int) -> Path | None:
    return _identity._process_executable_path(pid)


configure_service = configure_process_identity_service
reset_service = reset_process_identity_service

__all__ = [
    "LaunchProvenance",
    "LaunchProvenanceStore",
    "ProcessIdentity",
    "ProcessIdentityService",
    "clear_launches_for_tests",
    "configure_process_identity_service",
    "configure_service",
    "get_process_identity_service",
    "identity_for_pid",
    "record_launch",
    "reset_process_identity_service",
    "reset_service",
    "use_process_identity_service",
]
