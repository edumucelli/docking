"""Application identity parsing and instance-owned launch provenance."""

from __future__ import annotations

import subprocess
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, TypeGuard

APP_URI_PREFIX = "application://"
DEFAULT_MAX_LAUNCH_RECORDS = 256

ExecutableResolver = Callable[[int], Path | None]


@dataclass(frozen=True, slots=True)
class LaunchProvenance:
    """Desktop launcher identity retained for a process started by Docking."""

    desktop_id: str
    executable_path: Path | None = None


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Process evidence that can refine application-family matching."""

    pid: int
    executable_path: Path | None = None
    launch: LaunchProvenance | None = None


@dataclass(slots=True)
class _LaunchRecord:
    process: subprocess.Popen[Any]
    provenance: LaunchProvenance


def parse_application_uri(app_uri: str) -> str | None:
    """Return the desktop ID from a valid Unity ``application://`` URI."""
    if not app_uri.startswith(APP_URI_PREFIX):
        return None
    desktop_id = app_uri[len(APP_URI_PREFIX) :].strip()
    if not desktop_id or "/" in desktop_id or not desktop_id.endswith(".desktop"):
        return None
    return desktop_id


def _valid_pid(pid: object) -> TypeGuard[int]:
    """Preserve the legacy positive-int PID contract, excluding booleans."""
    return isinstance(pid, int) and not isinstance(pid, bool) and pid > 0


def _process_finished(process: subprocess.Popen[Any]) -> bool:
    """Return whether ``Popen.poll`` produced a real integer exit status."""
    try:
        status = process.poll()
    except (OSError, ValueError):
        return False
    return isinstance(status, int) and not isinstance(status, bool)


class LaunchProvenanceStore:
    """Thread-safe bounded launch records owned by one application graph."""

    def __init__(self, max_records: int = DEFAULT_MAX_LAUNCH_RECORDS) -> None:
        if (
            not isinstance(max_records, int)
            or isinstance(max_records, bool)
            or max_records <= 0
        ):
            raise ValueError("max_records must be a positive integer")
        self._max_records = max_records
        self._records: OrderedDict[int, _LaunchRecord] = OrderedDict()
        self._lock = RLock()

    @property
    def max_records(self) -> int:
        """Return the configured record bound."""
        with self._lock:
            return self._max_records

    def record_launch(
        self,
        *,
        process: subprocess.Popen[Any],
        desktop_id: str,
        executable_path: Path | None,
    ) -> None:
        """Remember the exact process created for a desktop launcher."""
        pid = getattr(process, "pid", None)
        if not _valid_pid(pid):
            return

        with self._lock:
            self._prune_finished_locked()
            self._records[pid] = _LaunchRecord(
                process=process,
                provenance=LaunchProvenance(
                    desktop_id=desktop_id,
                    executable_path=executable_path,
                ),
            )
            self._records.move_to_end(pid)
            while len(self._records) > self._max_records:
                self._records.popitem(last=False)

    def provenance_for_pid(self, pid: int | None) -> LaunchProvenance | None:
        """Return live launch provenance for an exact PID."""
        if not _valid_pid(pid):
            return None

        with self._lock:
            record = self._records.get(pid)
            if record is None:
                return None
            if _process_finished(record.process):
                self._records.pop(pid, None)
                return None
            self._records.move_to_end(pid)
            return record.provenance

    def prune_finished(self) -> None:
        """Discard every record whose exact process has finished."""
        with self._lock:
            self._prune_finished_locked()

    def clear(self) -> None:
        """Discard all records."""
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        """Return the current record count under the store lock."""
        with self._lock:
            return len(self._records)

    def _prune_finished_locked(self) -> None:
        """Prune records while the caller holds ``_lock``."""
        for pid, record in tuple(self._records.items()):
            if _process_finished(record.process):
                self._records.pop(pid, None)


class ProcessIdentityService:
    """Resolve process evidence against one shared provenance store."""

    def __init__(
        self,
        provenance_store: LaunchProvenanceStore,
        *,
        executable_resolver: ExecutableResolver | None = None,
    ) -> None:
        self._provenance_store = provenance_store
        self._executable_resolver = (
            executable_resolver
            if executable_resolver is not None
            else _process_executable_path
        )

    @property
    def provenance_store(self) -> LaunchProvenanceStore:
        """Return the store shared with the application launcher."""
        return self._provenance_store

    def identity_for_pid(self, pid: int | None) -> ProcessIdentity | None:
        """Return available identity evidence for *pid* without raising."""
        if not _valid_pid(pid):
            return None
        try:
            executable_path = self._executable_resolver(pid)
        except (OSError, RuntimeError):
            executable_path = None
        return ProcessIdentity(
            pid=pid,
            executable_path=executable_path,
            launch=self._provenance_store.provenance_for_pid(pid),
        )


def _process_executable_path(pid: int) -> Path | None:
    """Resolve one Linux process executable through ``/proc``."""
    try:
        path = (Path("/proc") / str(pid) / "exe").resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() else None


__all__ = [
    "APP_URI_PREFIX",
    "DEFAULT_MAX_LAUNCH_RECORDS",
    "ExecutableResolver",
    "LaunchProvenance",
    "LaunchProvenanceStore",
    "ProcessIdentity",
    "ProcessIdentityService",
    "parse_application_uri",
]
