"""Windows camera activity and journaled machine camera policy controls."""

from __future__ import annotations

import os
import threading
from pathlib import PureWindowsPath
from typing import Protocol

from docking.applets.camshield.backend import CamshieldBackend
from docking.applets.camshield.implementations.windows.client import (
    CameraSnapshot,
    WindowsCameraAPI,
)
from docking.applets.camshield.implementations.windows.control import (
    CameraControlState,
    WindowsCameraControl,
)
from docking.applets.camshield.state import CameraHolder, CamshieldState
from docking.i18n import _
from docking.log import get_logger
from docking.platform.native.windows.process import NativeProcessReader

log = get_logger("camshield.windows")


class CameraAPI(Protocol):
    def snapshot(self) -> CameraSnapshot: ...
    def stop(self) -> None: ...


class CameraControl(Protocol):
    def snapshot(self) -> CameraControlState: ...
    def available(self) -> bool: ...
    def perform(self, action: str) -> bool: ...
    def stop(self) -> None: ...


class WindowsCamshieldBackend(CamshieldBackend):
    def __init__(
        self, *, api: CameraAPI | None = None, control: CameraControl | None = None
    ) -> None:
        self._api = api if api is not None else WindowsCameraAPI()
        self._control = control if control is not None else WindowsCameraControl()
        self._control_state = CameraControlState()
        self._control_known = False
        self._devices_present = False
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._processes = NativeProcessReader()

    def get_state(self) -> CamshieldState:
        with self._lock:
            if self._stopped.is_set():
                return CamshieldState(False, False)
            self._control_known = False
            try:
                self._control_state = self._control.snapshot()
                self._control_known = True
            except OSError as exc:
                log.debug("Camera controls unavailable: %s", exc)
                self._control_state = CameraControlState()
            locked = self._control_state.policy.blocked
            self._devices_present = False
            try:
                snapshot = self._api.snapshot()
                if self._stopped.is_set():
                    return CamshieldState(False, False)
                self._devices_present = bool(snapshot.devices)
                if not snapshot.devices:
                    return CamshieldState(False, False, locked=locked)
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
                        # A successfully delivered empty aggregate is Windows'
                        # idle notification (also sent after the last client
                        # stops). A missing entry in a nonempty/failed report
                        # cannot establish this camera's activity.
                        if reports or not snapshot.activity_known:
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
                    locked=locked,
                )
            except OSError as exc:
                log.debug("Camera state unavailable: %s", exc)
                return CamshieldState(False, None, locked=locked, error=True)

    def controls_available(self) -> bool:
        return (
            not self._stopped.is_set()
            and self._control_known
            and self._control.available()
            and (
                self._control_state.policy.supported
                or self._control_state.recorded is not None
            )
        )

    def lock_available(self) -> bool:
        return (
            self.controls_available()
            and self._devices_present
            and self._control_state.policy.supported
            and not self._control_state.policy.blocked
        )

    def unlock_available(self) -> bool:
        return self.controls_available() and self._control_state.recorded is not None

    def lock(self) -> bool:
        return self._perform("lock")

    def unlock(self) -> bool:
        return self._perform("unlock")

    def _perform(self, action: str) -> bool:
        with self._lock:
            if not (
                self.lock_available() if action == "lock" else self.unlock_available()
            ):
                return False
            try:
                accepted = self._control.perform(action)
                state = self.get_state()
                return (
                    not self._stopped.is_set()
                    and self._control_known
                    and accepted
                    and (
                        state.locked
                        if action == "lock"
                        else self._control_state.recorded is None and not state.locked
                    )
                )
            except (OSError, TimeoutError) as exc:
                log.debug("Camera policy action failed: %s", exc)
                return False

    def stop(self) -> None:
        if self._stopped.is_set():
            return
        self._stopped.set()
        self._control.stop()
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
