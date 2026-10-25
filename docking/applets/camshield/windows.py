"""Passive Windows camera inventory/activity and explicit privacy Settings.

Device disabling needs a separate, elevated recovery-capable implementation.
Until that exists, this backend does not advertise lock/unlock controls.
"""

from __future__ import annotations

import os
import threading
from pathlib import PureWindowsPath
from typing import Protocol

from docking.applets.camshield.backend import CamshieldBackend
from docking.applets.camshield.state import CameraHolder, CamshieldState
from docking.applets.camshield.windows_client import CameraSnapshot, WindowsCameraAPI
from docking.i18n import _
from docking.log import get_logger
from docking.platform.applications.windows_process import WindowsProcessBackend

log = get_logger("camshield.windows")


class CameraAPI(Protocol):
    def snapshot(self) -> CameraSnapshot: ...
    def stop(self) -> None: ...


class WindowsCamshieldBackend(CamshieldBackend):
    def __init__(self, *, api: CameraAPI | None = None) -> None:
        self._api = api if api is not None else WindowsCameraAPI()
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._processes = WindowsProcessBackend()

    def get_state(self) -> CamshieldState:
        with self._lock:
            if self._stopped.is_set():
                return CamshieldState(False, False)
            try:
                snapshot = self._api.snapshot()
                if self._stopped.is_set() or not snapshot.devices:
                    return CamshieldState(False, False)
                reports = (
                    {row.identifier.casefold(): row for row in snapshot.reports}
                    if snapshot.activity_known
                    else {}
                )
                owners: dict[int, set[str]] = {}
                known = True
                for device in snapshot.devices:
                    activity = reports.get(device.identifier.casefold())
                    if activity is None:
                        known = False
                        continue
                    for pid in activity.pids:
                        owners.setdefault(pid, set()).add(device.name)
                holders = []
                for pid, devices in sorted(owners.items()):
                    executable = self._processes.executable(pid)
                    command = (
                        PureWindowsPath(str(executable)).name
                        if executable
                        else _("Unknown")
                    )
                    holders.append(CameraHolder(pid, command, tuple(sorted(devices))))
                return CamshieldState(
                    available=True,
                    active=True if holders else False if known else None,
                    devices=tuple(d.name for d in snapshot.devices),
                    holders=tuple(holders),
                )
            except OSError as exc:
                log.debug("Camera state unavailable: %s", exc)
                return CamshieldState(False, None, error=True)

    def controls_available(self) -> bool:
        return False

    def lock(self) -> bool:
        return False

    def unlock(self) -> bool:
        return False

    def stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._api.stop()

    def settings_available(self) -> bool:
        return not self._stopped.is_set()

    def open_settings(self) -> bool:
        if self._stopped.is_set():
            return False
        try:
            os.startfile("ms-settings:privacy-webcam")
            return True
        except OSError as exc:
            log.debug("Camera Settings unavailable: %s", exc)
            return False
