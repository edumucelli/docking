"""Camera observation and serialized permission changes shared by BSDs."""

from __future__ import annotations

import fcntl
import re
from abc import abstractmethod
from collections import defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from docking.applets.camshield.implementations.unix.control import UnixCameraControl
from docking.applets.camshield.state import CameraHolder, CamshieldState
from docking.platform.commands import read_command

_HOLDER = re.compile(r"^\s*\S+\s+(.+?)\s+(\d+)\s+(\d+)\*?\s+.*?\s+(/\S+)\s*$")


@contextmanager
def _action_lock(state_dir: Path) -> Generator[None, None, None]:
    from docking.applets.camshield.implementations.unix.helper import _require_root

    _require_root()
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (state_dir / "action.lock").open("a") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        yield


def _holders(output: str, devices: dict[str, Path]) -> tuple[CameraHolder, ...]:
    by_pid: dict[int, set[str]] = defaultdict(set)
    names: dict[int, str] = {}
    allowed = {str(path) for path in devices.values()}
    for line in output.splitlines():
        match = _HOLDER.match(line)
        if match is None or match[4] not in allowed:
            continue
        pid = int(match[2])
        by_pid[pid].add(match[4])
        names[pid] = match[1].strip()
    return tuple(
        CameraHolder(pid, names[pid], tuple(sorted(paths)))
        for pid, paths in sorted(by_pid.items())
    )


def probe_devices(
    devices: dict[str, Path], *, locked: Callable[[], bool]
) -> CamshieldState:
    if not devices:
        return CamshieldState(available=False, active=False)
    # Both supported camera integrations use fstat's kernel open-file table.
    # Explicit paths add NAME to the output, avoiding inode guesses.
    output = read_command(["fstat", *(str(path) for path in devices.values())])
    if output is None:
        # Never turn a failed inspection into a reassuring "Camera idle".
        return CamshieldState(available=False, active=None, error=True)
    holders = _holders(output, devices)
    return CamshieldState(
        True, bool(holders), tuple(sorted(devices)), holders, locked()
    )


class BsdCameraControl(UnixCameraControl):
    controls_supported = True
    state_dir = Path("/var/db/docking/camshield")

    def requires_acl_backup(self, device: Path) -> bool:
        # devfs camera nodes have mode permissions but no extended ACLs. Refuse
        # a failed backup only where ACLs exist, or their support is unknown.
        return any(
            read_command(["/usr/bin/getconf", kind, str(device)]) != "0"
            for kind in ("ACL_EXTENDED", "ACL_NFS4")
        )

    def lock(self) -> int:
        with _action_lock(self.state_dir):
            return self._lock_devices()

    def unlock(self) -> int:
        with _action_lock(self.state_dir):
            return self._unlock_devices()

    @abstractmethod
    def _lock_devices(self) -> int: ...

    @abstractmethod
    def _unlock_devices(self) -> int: ...

    def acl_commands(
        self, device: Path, acl_file: Path
    ) -> tuple[list[str], list[list[str]]]:
        return (
            ["getfacl", "-q", str(device)],
            [
                ["setfacl", "-b", str(device)],
                ["setfacl", "-M", str(acl_file), str(device)],
            ],
        )
