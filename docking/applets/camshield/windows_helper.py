"""Elevated camera policy actions with write-ahead recovery of its prior value."""

from __future__ import annotations

import argparse
import ctypes
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Protocol

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from docking.applets.camshield.windows_journal import (
    RecordedPolicy,
    WindowsCameraJournal,
)
from docking.applets.camshield.windows_policy import (
    CameraPolicyState,
    WindowsCameraPolicy,
)
from docking.platform.backends.windows.ffi import bind


class Policy(Protocol):
    def snapshot(self) -> CameraPolicyState: ...
    def set_value(self, value: int | None) -> bool: ...


class Journal(Protocol):
    def read(self) -> RecordedPolicy | None: ...
    def write(self, record: RecordedPolicy | None) -> None: ...


class CameraTransitions:
    def __init__(self, *, policy: Policy, journal: Journal) -> None:
        self.policy, self.journal = policy, journal

    def lock(self) -> bool:
        record = self.journal.read()
        state = self.policy.snapshot()
        if not state.supported:
            return False
        if state.blocked:
            # A restriction set elsewhere never becomes ours to remove.
            return record is not None
        self.journal.write(RecordedPolicy(state.value))
        accepted = self.policy.set_value(0)
        return accepted and self.policy.snapshot().blocked

    def unlock(self) -> bool:
        record = self.journal.read()
        if record is None:
            return False
        state = self.policy.snapshot()
        if state.blocked:
            if not self.policy.set_value(record.value):
                return False
            if self.policy.snapshot().value != record.value:
                return False
        # If another administrator already removed/replaced our restriction,
        # keep their current value rather than replaying a stale baseline.
        self.journal.write(None)
        return True


@contextmanager
def _exclusive() -> Iterator[None]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = bind(
        kernel,
        "CreateMutexW",
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.c_wchar_p,
    )(None, False, "Global\\DockingCamShieldControl")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        status = bind(
            kernel,
            "WaitForSingleObject",
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
        )(handle, 10000)
        if status not in (0, 0x80):
            raise OSError("Another camera action is still running")
        acquired = True
        yield
    finally:
        if acquired:
            bind(kernel, "ReleaseMutex", ctypes.c_int32, ctypes.c_void_p)(handle)
        bind(kernel, "CloseHandle", ctypes.c_int32, ctypes.c_void_p)(handle)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("lock", "unlock"))
    args = parser.parse_args()
    if sys.platform != "win32" or not ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Camera policy changes require an elevated Windows helper")
    try:
        with _exclusive():
            actions = CameraTransitions(
                policy=WindowsCameraPolicy(), journal=WindowsCameraJournal()
            )
            return 0 if getattr(actions, args.action)() else 1
    except OSError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
