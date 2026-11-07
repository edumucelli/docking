"""NetBSD camera control availability before a native integration exists."""

import json
from pathlib import Path

from docking.applets.camshield.implementations.unix.control import UnixCameraControl
from docking.applets.camshield.state import CamshieldState

_UNAVAILABLE = "NetBSD camera permission control unavailable"


class NetBsdCameraControl(UnixCameraControl):
    def probe(self, *, dev_root: Path, proc_root: Path) -> CamshieldState:
        return CamshieldState(available=False, active=None, error=True)

    def lock(self) -> int:
        raise OSError(_UNAVAILABLE)

    def unlock(self) -> int:
        raise OSError(_UNAVAILABLE)

    def status(self) -> int:
        print(json.dumps({"available": False, "locked": False, "devices": []}))
        return 0

    def requires_acl_backup(self, device: Path) -> bool:
        raise OSError(_UNAVAILABLE)

    def acl_commands(
        self, device: Path, acl_file: Path
    ) -> tuple[list[str], list[list[str]]]:
        raise OSError(_UNAVAILABLE)
