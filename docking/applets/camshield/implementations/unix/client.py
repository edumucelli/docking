"""Unix unprivileged camera client and authorized helper launch."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from docking.applets.camshield.backend import CamshieldBackend
from docking.log import get_logger

from ...state import CamshieldState
from .control import UnixCameraControl

log = get_logger("camshield")
_HELPER_BIN = "docking-camshield-helper"
_PKEXEC_BIN = "pkexec"
_SOURCE_HELPER = Path(__file__).resolve().parents[2] / "helper.py"


def _helper_command(*, action: str) -> list[str] | None:
    pkexec = shutil.which(_PKEXEC_BIN)
    if pkexec is None:
        return None
    helper = shutil.which(_HELPER_BIN)
    if helper is not None:
        return [pkexec, helper, action]
    if _SOURCE_HELPER.exists():
        return [pkexec, sys.executable, str(_SOURCE_HELPER), action]
    return None


class UnixCamshieldClient(CamshieldBackend):
    def __init__(
        self,
        control: UnixCameraControl,
        *,
        dev_root: Path = Path("/dev"),
        proc_root: Path = Path("/proc"),
    ) -> None:
        self._control = control
        self._dev_root = dev_root
        self._proc_root = proc_root

    def probe(self) -> CamshieldState:
        return self._control.probe(dev_root=self._dev_root, proc_root=self._proc_root)

    def helper_available(self) -> bool:
        return _helper_command(action="status") is not None

    def perform(self, action: str) -> bool:
        command = _helper_command(action=action)
        if command is None:
            log.warning("Camera lock helper unavailable")
            return False
        try:
            completed = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            log.exception("Failed to run camera lock helper")
            return False
        if completed.returncode != 0:
            log.warning(
                "Camera lock helper exited with status %s", completed.returncode
            )
        # A failed helper may still have changed some devices; re-probe it.
        return True
