"""FreeBSD camera holders and device permission recovery."""

from pathlib import Path

from docking.applets.camshield.implementations.unix.devices import (
    enumerate_video_devices,
)
from docking.applets.camshield.state import CamshieldState

from .common import BsdCameraControl, probe_devices


class FreeBsdCameraControl(BsdCameraControl):
    def probe(self, *, dev_root: Path, proc_root: Path) -> CamshieldState:
        devices = enumerate_video_devices(dev_root)
        return probe_devices(
            devices,
            locked=lambda: all(
                path.stat().st_mode & 0o777 == 0 for path in devices.values()
            ),
        )

    def _lock_devices(self) -> int:
        from docking.applets.camshield.implementations.unix.helper import lock_devices

        return lock_devices(backend=self, state_dir=self.state_dir)

    def _unlock_devices(self) -> int:
        from docking.applets.camshield.implementations.unix.helper import unlock_devices

        return unlock_devices(backend=self, state_dir=self.state_dir)

    def status(self) -> int:
        from docking.applets.camshield.implementations.unix.helper import status_devices

        return status_devices(state_dir=self.state_dir)
