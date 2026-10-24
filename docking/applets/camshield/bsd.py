"""BSD camera holders, ACLs, and OpenBSD's kernel recording switch."""

from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from docking.applets.camshield.backend import CamshieldBackend
from docking.applets.camshield.state import CameraHolder, CamshieldState, _video_devices
from docking.platform.commands import read_command

_STATE_DIR = Path("/var/db/docking/camshield")
_RECORDING = "kern.video.record"
_HOLDER = re.compile(r"^\s*\S+\s+(.+?)\s+(\d+)\s+(\d+)\*?\s+.*?\s+(/\S+)\s*$")


def _devices(dev_root: Path) -> dict[str, Path]:
    devices = _video_devices(dev_root)
    if sys.platform.startswith("openbsd"):
        # OpenBSD creates video nodes even when no driver is attached.
        for name, path in tuple(devices.items()):
            try:
                descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            except OSError as exc:
                if exc.errno in {errno.ENXIO, errno.ENODEV}:
                    del devices[name]
            else:
                os.close(descriptor)
    return devices


@contextmanager
def _action_lock():
    from docking.applets.camshield.helper import _require_root

    _require_root()
    _STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (_STATE_DIR / "action.lock").open("a") as stream:
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


def _set_recording(value: int) -> None:
    subprocess.run(
        ["/sbin/sysctl", f"{_RECORDING}={value}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    )


class BsdCamshieldBackend(CamshieldBackend):
    def requires_acl_backup(self, device: Path) -> bool:
        # devfs camera nodes have mode permissions but no extended ACLs. Refuse
        # a failed backup only where ACLs exist, or their support is unknown.
        return any(
            read_command(["/usr/bin/getconf", kind, str(device)]) != "0"
            for kind in ("ACL_EXTENDED", "ACL_NFS4")
        )

    def probe(self, *, dev_root: Path, proc_root: Path) -> CamshieldState:
        devices = _devices(dev_root)
        if not devices:
            return CamshieldState(available=False, active=False)
        # Both BSDs expose the kernel's open-file table through fstat. Passing
        # explicit paths also adds NAME to each row, avoiding inode guesses.
        output = read_command(["fstat", *(str(path) for path in devices.values())])
        if output is None:
            # Never turn a failed inspection into a reassuring "Camera idle".
            return CamshieldState(available=False, active=False)
        holders = _holders(output, devices)
        locked = (
            read_command(["/sbin/sysctl", "-n", _RECORDING]) == "0"
            if sys.platform.startswith("openbsd")
            else all(path.stat().st_mode & 0o777 == 0 for path in devices.values())
        )
        return CamshieldState(
            True, bool(holders), tuple(sorted(devices)), holders, locked
        )

    def lock(self) -> int:
        with _action_lock():
            return self._lock_devices()

    def _lock_devices(self) -> int:
        if not sys.platform.startswith("openbsd"):
            from docking.applets.camshield.helper import lock_devices

            return lock_devices(state_dir=_STATE_DIR)
        from docking.applets.camshield.helper import _require_root

        _require_root()
        previous = read_command(["/sbin/sysctl", "-n", _RECORDING])
        if previous not in {"0", "1"}:
            raise OSError("Cannot read the kernel video recording policy")
        _STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = _STATE_DIR / "recording.json"
        if not state.exists():
            # Save before changing policy; repeated locks preserve the original.
            descriptor = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump({"previous": int(previous)}, stream)
        _set_recording(0)
        return 0

    def unlock(self) -> int:
        with _action_lock():
            return self._unlock_devices()

    def _unlock_devices(self) -> int:
        if not sys.platform.startswith("openbsd"):
            from docking.applets.camshield.helper import unlock_devices

            return unlock_devices(state_dir=_STATE_DIR)
        from docking.applets.camshield.helper import _require_root

        _require_root()
        state = _STATE_DIR / "recording.json"
        try:
            previous = json.loads(state.read_text())["previous"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise OSError("No saved video recording policy to restore") from exc
        if type(previous) is not int or previous not in {0, 1}:
            raise OSError("Invalid saved video recording policy")
        _set_recording(previous)
        state.unlink()
        return 0

    def status(self) -> int:
        if not sys.platform.startswith("openbsd"):
            from docking.applets.camshield.helper import status_devices

            return status_devices(state_dir=_STATE_DIR)
        value = read_command(["/sbin/sysctl", "-n", _RECORDING])
        if value not in {"0", "1"}:
            raise OSError("Cannot read the kernel video recording policy")
        print(
            json.dumps(
                {
                    "available": bool(_devices(Path("/dev"))),
                    "locked": value == "0",
                    "devices": [],
                }
            )
        )
        return 0

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
