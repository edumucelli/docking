"""Unprivileged camera control state and explicit elevated action dispatch."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from docking.applets.camshield.implementations.windows.elevation import (
    helper_available,
    run_action,
)
from docking.applets.camshield.implementations.windows.journal import (
    RecordedPolicy,
    WindowsCameraJournal,
)
from docking.applets.camshield.implementations.windows.policy import (
    CameraPolicyState,
    WindowsCameraPolicy,
)


@dataclass(frozen=True)
class CameraControlState:
    policy: CameraPolicyState = field(default_factory=CameraPolicyState)
    recorded: RecordedPolicy | None = None


class WindowsCameraControl:
    def __init__(self) -> None:
        self._policy = WindowsCameraPolicy()
        self._journal = WindowsCameraJournal()
        self._stopped = threading.Event()
        self._known = False

    def snapshot(self) -> CameraControlState:
        if self._stopped.is_set():
            return CameraControlState()
        self._known = False
        state = CameraControlState(self._policy.snapshot(), self._journal.read())
        self._known = True
        return state

    def available(self) -> bool:
        # Menus use only cached capability. No registry reads on GTK.
        return not self._stopped.is_set() and self._known and helper_available()

    def perform(self, action: str) -> bool:
        return self.available() and action in ("lock", "unlock") and run_action(action)

    def stop(self) -> None:
        self._stopped.set()
