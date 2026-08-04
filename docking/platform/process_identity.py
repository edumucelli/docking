"""Runtime process identity and Docking launch provenance."""

from __future__ import annotations

import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MAX_LAUNCH_RECORDS = 256


@dataclass(frozen=True)
class LaunchProvenance:
    """Desktop launcher identity retained for a process started by Docking."""

    desktop_id: str
    executable_path: Path | None = None


@dataclass(frozen=True)
class ProcessIdentity:
    """Process evidence that can refine application-family matching."""

    pid: int
    executable_path: Path | None = None
    launch: LaunchProvenance | None = None


@dataclass
class _LaunchRecord:
    process: subprocess.Popen[Any]
    provenance: LaunchProvenance


_launches: OrderedDict[int, _LaunchRecord] = OrderedDict()


def record_launch(
    *,
    process: subprocess.Popen[Any],
    desktop_id: str,
    executable_path: Path | None,
) -> None:
    """Remember the exact process created for a desktop launcher.

    Shell launchers commonly replace themselves with ``exec``. The PID remains
    stable across that replacement, so retaining the ``Popen`` PID preserves
    the original desktop identity without leaking it to unrelated descendants.
    """
    pid = getattr(process, "pid", None)
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return
    _prune_finished_launches()
    _launches[pid] = _LaunchRecord(
        process=process,
        provenance=LaunchProvenance(
            desktop_id=desktop_id,
            executable_path=executable_path,
        ),
    )
    _launches.move_to_end(pid)
    while len(_launches) > _MAX_LAUNCH_RECORDS:
        _launches.popitem(last=False)


def identity_for_pid(pid: int | None) -> ProcessIdentity | None:
    """Return available identity evidence for *pid* without raising."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return None
    return ProcessIdentity(
        pid=pid,
        executable_path=_process_executable_path(pid),
        launch=_launch_provenance(pid),
    )


def clear_launches_for_tests() -> None:
    """Clear process launch records between isolated tests."""
    _launches.clear()


def _launch_provenance(pid: int) -> LaunchProvenance | None:
    record = _launches.get(pid)
    if record is None:
        return None
    if _process_finished(record.process):
        _launches.pop(pid, None)
        return None
    _launches.move_to_end(pid)
    return record.provenance


def _process_finished(process: subprocess.Popen[Any]) -> bool:
    try:
        status = process.poll()
    except (OSError, ValueError):
        return False
    return isinstance(status, int) and not isinstance(status, bool)


def _prune_finished_launches() -> None:
    for pid, record in list(_launches.items()):
        if _process_finished(record.process):
            _launches.pop(pid, None)


def _process_executable_path(pid: int) -> Path | None:
    try:
        path = (Path("/proc") / str(pid) / "exe").resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() else None
