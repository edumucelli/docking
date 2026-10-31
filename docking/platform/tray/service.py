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

"""One poll loop for native tray state, shared by its consumers."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.applets.worker import SerialWorker
from docking.log import get_logger, with_context

from .backend import TrayBackend
from .menu import TrayMenuClient
from .state import StatusTrayState

POLL_INTERVAL_S = 3
_POLL_KEY = "status-notifier-poll"

log = with_context(get_logger(name="status_notifier"), component="service")

TrayStateListener = Callable[[StatusTrayState], bool | None]


class TrayService:
    """Own the native backend and publish its polled state.

    State is read on a worker thread and delivered on the main loop, so
    listeners may touch GTK. Composition owns the service; consumers subscribe
    without taking ownership. Restart creates a fresh backend and worker, and
    callbacks from a previous lifetime cannot update the current state.
    """

    def __init__(
        self,
        *,
        interval_s: int = POLL_INTERVAL_S,
        backend_factory: Callable[[], TrayBackend],
        worker_factory: Callable[..., SerialWorker] = SerialWorker,
    ) -> None:
        self._backend_factory = backend_factory
        self._worker_factory = worker_factory
        self._backend: TrayBackend | None = backend_factory()
        self._worker = worker_factory(on_close=self._backend.close, logger=log)
        self._generation = 0
        self._interval_s = interval_s
        self._listeners: list[TrayStateListener] = []
        self._state = self._backend.initial_state()
        self._timer_id = 0
        self._running = False

    @property
    def state(self) -> StatusTrayState:
        """Return the most recently delivered tray state."""
        return self._state

    def add_listener(self, listener: TrayStateListener) -> None:
        """Subscribe to state changes, delivering the current state at once."""
        if listener not in self._listeners:
            self._listeners.append(listener)
        listener(self._state)

    def remove_listener(self, listener: TrayStateListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def start(self) -> None:
        """Poll immediately, then on every interval until stopped."""
        if self._running:
            return
        if self._backend is None:
            self._backend = self._backend_factory()
            self._worker = self._worker_factory(
                on_close=self._backend.close, logger=log
            )
        self._generation += 1
        self._running = True
        self.refresh()
        self._timer_id = GLib.timeout_add_seconds(self._interval_s, self._tick)

    def stop(self) -> None:
        """Stop polling and release the backend; repeated calls do nothing."""
        if self._backend is None:
            return
        self._running = False
        self._generation += 1
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._listeners.clear()
        self._backend = None
        self._worker.stop()

    def refresh(self) -> None:
        """Request an immediate poll; a request in flight is not duplicated."""
        if not self._running:
            return
        backend = self._backend
        if backend is None:
            return
        generation = self._generation
        self._worker.run_guarded(
            key=f"{_POLL_KEY}:{generation}",
            name="status-notifier-poll",
            fn=backend.get_state,
            on_result=lambda state: (
                self._on_state_result(state)
                if generation == self._generation
                else False
            ),
            on_error=lambda exc: (
                self._on_poll_error(exc) if generation == self._generation else False
            ),
        )

    def activate(self, identifier: str) -> bool:
        return (
            self._backend.activate(identifier) if self._backend is not None else False
        )

    def context_menu(self, identifier: str) -> bool:
        return (
            self._backend.context_menu(identifier)
            if self._backend is not None
            else False
        )

    def menu_client(self, identifier: str) -> TrayMenuClient | None:
        return (
            self._backend.menu_client(identifier) if self._backend is not None else None
        )

    def _tick(self) -> bool:
        self.refresh()
        return self._running

    def _on_state_result(self, state: StatusTrayState) -> bool:
        if not self._running:
            return False
        self._state = state
        for listener in tuple(self._listeners):
            listener(state)
        return False

    def _on_poll_error(self, exc: Exception) -> bool:
        if self._running:
            log.bind(action="poll").debug("StatusNotifier poll failed: %s", exc)
        return False
