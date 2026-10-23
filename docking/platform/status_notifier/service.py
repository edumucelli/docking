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

"""One poll loop for StatusNotifier tray state, shared by its consumers.

The launcher-badge bridge and the System Tray applet both display the same tray
state. While each of them owned a backend and a three second timer, every item
was enumerated twice per cycle, on two D-Bus connections, in phase. Consumers
subscribe to this service instead, and the poll happens once.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.applets.worker import BackgroundWorker
from docking.log import get_logger, with_context
from docking.platform.status_notifier.backend import (
    StatusNotifierBackend,
    StatusTrayState,
    unavailable_state,
)

if TYPE_CHECKING:
    from docking.platform.status_notifier.dbusmenu import DBusMenuClient

POLL_INTERVAL_S = 3
_POLL_KEY = "status-notifier-poll"

log = with_context(get_logger(name="status_notifier"), component="service")

TrayStateListener = Callable[[StatusTrayState], bool | None]


class StatusNotifierService:
    """Own the StatusNotifier backend and publish its polled state.

    State is read on a worker thread and delivered on the main loop, so
    listeners may touch GTK. A service can be started once and shared by
    several consumers; a consumer that creates its own keeps the previous
    behaviour and owns its lifetime.
    """

    def __init__(
        self,
        *,
        interval_s: int = POLL_INTERVAL_S,
        backend: StatusNotifierBackend | None = None,
        worker: BackgroundWorker | None = None,
    ) -> None:
        self._backend = StatusNotifierBackend() if backend is None else backend
        self._worker = BackgroundWorker(logger=log) if worker is None else worker
        self._interval_s = interval_s
        self._listeners: list[TrayStateListener] = []
        self._state = unavailable_state()
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
        self._running = True
        self.refresh()
        self._timer_id = GLib.timeout_add_seconds(self._interval_s, self._tick)

    def stop(self) -> None:
        """Stop polling and release the backend; repeated calls do nothing."""
        if not self._running:
            return
        self._running = False
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._listeners.clear()
        self._backend.close()

    def refresh(self) -> None:
        """Request an immediate poll; a request in flight is not duplicated."""
        if not self._running:
            return
        self._worker.run_guarded(
            key=_POLL_KEY,
            name="status-notifier-poll",
            fn=self._backend.get_state,
            on_result=self._on_state_result,
            on_error=self._on_poll_error,
        )

    def activate(self, identifier: str) -> bool:
        return self._backend.activate(identifier)

    def context_menu(self, identifier: str) -> bool:
        return self._backend.context_menu(identifier)

    def menu_client(self, identifier: str) -> DBusMenuClient | None:
        return self._backend.menu_client(identifier)

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
