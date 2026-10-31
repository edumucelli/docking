"""Unprivileged Unix camera client and policy-mediated helper launch."""

from __future__ import annotations

import shutil
import subprocess
import sys
import threading
from pathlib import Path

from docking.applets.camshield.backend import CamshieldBackend
from docking.applets.camshield.state import CamshieldState
from docking.applets.camshield.unix_control import UnixCameraControl
from docking.log import get_logger

log = get_logger("camshield.unix_client")
_HELPER_BIN = "docking-camshield-helper"
_PKEXEC_BIN = "pkexec"
_SOURCE_HELPER = Path(__file__).with_name("helper.py")


def _helper_command(*, action: str) -> list[str] | None:
    if action not in ("status", "lock", "unlock"):
        return None
    pkexec = shutil.which(_PKEXEC_BIN)
    if pkexec is None:
        return None
    helper = shutil.which(_HELPER_BIN)
    if helper is not None:
        return [pkexec, helper, action]
    if _SOURCE_HELPER.exists():
        return [pkexec, sys.executable, str(_SOURCE_HELPER), action]
    return None


class UnixCamshieldBackend(CamshieldBackend):
    def __init__(
        self, *, control: UnixCameraControl, dev_root: Path, proc_root: Path
    ) -> None:
        self._control = control
        self._dev_root, self._proc_root = dev_root, proc_root
        self._lock = threading.RLock()
        self._stopped = threading.Event()

    def stop(self) -> None:
        self._stopped.set()

    def get_state(self) -> CamshieldState:
        with self._lock:
            if self._stopped.is_set():
                return CamshieldState(False, False)
            return self._control.probe(
                dev_root=self._dev_root, proc_root=self._proc_root
            )

    def controls_available(self) -> bool:
        return (
            not self._stopped.is_set() and _helper_command(action="status") is not None
        )

    def _perform(self, action: str) -> bool:
        with self._lock:
            if self._stopped.is_set():
                return False
            command = _helper_command(action=action)
            if command is None:
                return False
            try:
                result = subprocess.run(
                    command,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                )
                return result.returncode == 0 and not self._stopped.is_set()
            except (OSError, subprocess.TimeoutExpired) as exc:
                log.debug("Camera helper action failed: %s", exc)
                return False

    def lock(self) -> bool:
        return self._perform("lock")

    def unlock(self) -> bool:
        return self._perform("unlock")
