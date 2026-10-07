"""Application identity parsing and instance-owned launch provenance."""

from __future__ import annotations

import configparser
import re
import subprocess
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, TypeGuard

from .flatpak import valid_flatpak_app_id

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
    sandbox_app_id: str | None = None
    script_basename: str | None = None


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
    """Accept only positive integer PIDs, excluding booleans."""
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
        proc_root: Path = Path("/proc"),
    ) -> None:
        self._provenance_store = provenance_store
        self._proc_root = proc_root
        self._executable_resolver = (
            executable_resolver
            if executable_resolver is not None
            else lambda pid: _process_executable_path(pid, proc_root=self._proc_root)
        )

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
            sandbox_app_id=_process_sandbox_app_id(self._proc_root / str(pid)),
            script_basename=_process_python_script(self._proc_root / str(pid)),
        )


def _process_executable_path(
    pid: int, *, proc_root: Path = Path("/proc")
) -> Path | None:
    """Resolve one Linux process executable through ``/proc``."""
    try:
        path = (proc_root / str(pid) / "exe").resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return path if path.is_file() else None


def _bounded_process_file(path: Path, limit: int) -> bytes | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except (OSError, RuntimeError):
        return None
    return data if len(data) <= limit else None


def _process_sandbox_app_id(process_dir: Path) -> str | None:
    """Read the Flatpak identity in the process's own mount namespace."""
    data = _bounded_process_file(process_dir / "root" / ".flatpak-info", 65536)
    if data is None:
        return None
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(data.decode("utf-8"))
        app_id = parser.get("Application", "name", fallback="").strip()
    except (UnicodeDecodeError, configparser.Error):
        return None
    return app_id if valid_flatpak_app_id(app_id) else None


def _process_python_script(process_dir: Path) -> str | None:
    """Read only the script basename from a direct Python invocation."""
    data = _bounded_process_file(process_dir / "cmdline", 4096)
    if data is None:
        return None
    try:
        argv = data.rstrip(b"\0").decode("utf-8").split("\0")
    except UnicodeDecodeError:
        return None
    if len(argv) < 2 or not re.fullmatch(
        r"python(?:\d+(?:\.\d+)*)?", Path(argv[0]).name
    ):
        return None
    script = argv[1]
    if script.startswith("-") or not script.endswith(".py"):
        return None
    return Path(script).name


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
