"""OpenBSD camera holders and the kernel recording policy."""

from __future__ import annotations

import errno
import json
import os
import subprocess
from pathlib import Path

from docking.applets.camshield.implementations.unix.devices import (
    enumerate_video_devices,
)
from docking.applets.camshield.state import CamshieldState
from docking.platform.commands import read_command

from .common import BsdCameraControl, probe_devices

_RECORDING = "kern.video.record"


def _devices(dev_root: Path) -> dict[str, Path]:
    devices = enumerate_video_devices(dev_root)
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


def _set_recording(value: int) -> None:
    subprocess.run(
        ["/sbin/sysctl", f"{_RECORDING}={value}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    )


class OpenBsdCameraControl(BsdCameraControl):
    def probe(self, *, dev_root: Path, proc_root: Path) -> CamshieldState:
        return probe_devices(
            _devices(dev_root),
            locked=lambda: read_command(["/sbin/sysctl", "-n", _RECORDING]) == "0",
        )

    def _lock_devices(self) -> int:
        from docking.applets.camshield.implementations.unix.helper import _require_root

        _require_root()
        previous = read_command(["/sbin/sysctl", "-n", _RECORDING])
        if previous not in {"0", "1"}:
            raise OSError("Cannot read the kernel video recording policy")
        self.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = self.state_dir / "recording.json"
        if state.exists():
            _saved_recording(state)
        else:
            # Save before changing policy; repeated locks preserve the original.
            from docking.applets.camshield.implementations.unix.helper import (
                _atomic_write_json,
            )

            _atomic_write_json(state, {"previous": int(previous)})
        _set_recording(0)
        return 0

    def _unlock_devices(self) -> int:
        from docking.applets.camshield.implementations.unix.helper import _require_root

        _require_root()
        state = self.state_dir / "recording.json"
        previous = _saved_recording(state)
        _set_recording(previous)
        state.unlink()
        return 0

    def status(self) -> int:
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


def _saved_recording(state: Path) -> int:
    try:
        previous = json.loads(state.read_text())["previous"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise OSError("No saved video recording policy to restore") from exc
    if type(previous) is not int or previous not in {0, 1}:
        raise OSError("Invalid saved video recording policy")
    return previous
