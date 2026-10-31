# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Shared inhibition contract, platform selection and serialized requests."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from docking.applets.worker import BackgroundWorker
from docking.log import get_logger
from docking.platform import environment

log = get_logger("caffeine.inhibit")


class Inhibitor(Protocol):
    """Acquire/release contract for a wake lock. Calls belong on a worker."""

    @property
    def active(self) -> bool: ...
    def acquire(self) -> bool: ...
    def release(self) -> None: ...


class CompositeInhibitor:
    """Acquire independent locks without skipping later parts on failure."""

    def __init__(self, parts: tuple[Inhibitor, ...]) -> None:
        self._parts = parts

    @property
    def active(self) -> bool:
        return any(part.active for part in self._parts)

    def acquire(self) -> bool:
        results = [part.acquire() for part in self._parts]
        return any(results)

    def release(self) -> None:
        for part in self._parts:
            part.release()


def default_inhibitor() -> Inhibitor:
    if environment.is_windows():
        from docking.applets.caffeine.windows import WindowsInhibitor

        return WindowsInhibitor()
    from docking.applets.caffeine.linux import create_inhibitor

    return create_inhibitor()


@dataclass(frozen=True)
class InhibitionResult:
    version: int
    active: bool
    succeeded: bool


class InhibitionSession:
    """Reconcile the latest intent without relying on GTK to release a lock.

    A stop during a native call is observed by that same worker before it
    finishes. A stop after native completion starts an independent release,
    even if the older result has not yet been delivered on GTK.
    """

    def __init__(
        self,
        inhibitor: Inhibitor,
        on_result: Callable[[InhibitionResult], object],
        *,
        worker: BackgroundWorker | None = None,
    ) -> None:
        self._inhibitor = inhibitor
        self._on_result = on_result
        # A normal process exit must wait for release, including a Unix child
        # lock which could otherwise outlive its parent. No GTK callback is
        # needed for the worker to observe stop and finish cleanup.
        self._worker = (
            worker
            if worker is not None
            else BackgroundWorker(
                thread_factory=lambda target, daemon: threading.Thread(
                    target=target, daemon=False, name="caffeine-inhibit"
                )
            )
        )
        self._lock = threading.Lock()
        self._version = 0
        self._desired = False
        self._running = False
        self._stopped = False

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def pending(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> None:
        with self._lock:
            if self._stopped:
                self._stopped = False
                self._version += 1

    def request(self, active: bool) -> None:
        with self._lock:
            if self._stopped:
                return
            self._desired = active
            self._version += 1
            launch = not self._running
            self._running = True
        if launch:
            self._launch()

    def stop(self) -> None:
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            self._desired = False
            self._version += 1
            launch = not self._running
            self._running = True
        if launch:
            self._launch()

    def _launch(self) -> None:
        self._worker.run(
            name="caffeine-inhibit", fn=self._reconcile, on_result=self._on_result
        )

    def _reconcile(self) -> InhibitionResult:
        completed = False
        try:
            while True:
                with self._lock:
                    version, desired = (
                        self._version,
                        self._desired and not self._stopped,
                    )
                try:
                    if desired:
                        succeeded = self._inhibitor.acquire()
                    else:
                        self._inhibitor.release()
                        succeeded = not self._inhibitor.active
                    active = self._inhibitor.active
                except OSError as exc:
                    log.debug("Inhibition request failed: %s", exc)
                    active, succeeded = self._inhibitor.active, False
                with self._lock:
                    if version != self._version:
                        continue
                    completed = True
                    self._running = False
                    return InhibitionResult(
                        version, active, succeeded and active == desired
                    )
        finally:
            if not completed:
                with self._lock:
                    self._running = False
