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

from collections.abc import Callable, Mapping
from threading import Event

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib

from docking.applets.worker import SerialWorker
from docking.log import get_logger, with_context
from docking.platform.backends.base import Rect

from .backend import TrayBackend
from .menu import TrayMenuClient
from .state import TrayFocusRequest, TrayState

POLL_INTERVAL_S = 3
_POLL_KEY = "status-notifier-poll"

log = with_context(get_logger(name="status_notifier"), component="service")

TrayStateListener = Callable[[TrayState], bool | None]
TrayFocusListener = Callable[[TrayFocusRequest], None]


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
        self._worker: SerialWorker
        self._ready_to_read = Event()
        self._ready_to_read.set()
        self._lifetime_closed = self._ready_to_read
        self._backend: TrayBackend | None = self._new_backend()
        self._generation = 0
        self._interval_s = interval_s
        self._listeners: list[TrayStateListener] = []
        self._focus_listeners: list[TrayFocusListener] = []
        self._focus_generation = 0
        self._state = self._backend.initial_state()
        self._timer_id = 0
        self._running = False

    def _new_backend(self) -> TrayBackend:
        previous_closed, closed = self._lifetime_closed, Event()
        backend = self._backend_factory()

        def close() -> None:
            # An exclusive native host must finish its previous lifetime before
            # another worker starts or closes a successor. Never wait on GTK.
            previous_closed.wait()
            try:
                backend.close()
            finally:
                closed.set()

        self._worker = self._worker_factory(on_close=close, logger=log)
        self._ready_to_read, self._lifetime_closed = previous_closed, closed
        return backend

    @property
    def state(self) -> TrayState:
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

    @property
    def generation(self) -> int:
        """Identify the provider lifetime; stop/start always changes this token."""
        return self._generation

    def add_focus_listener(self, listener: TrayFocusListener) -> None:
        if listener not in self._focus_listeners:
            self._focus_listeners.append(listener)
            self._sync_focus_handler()

    def remove_focus_listener(self, listener: TrayFocusListener) -> None:
        if listener in self._focus_listeners:
            self._focus_listeners.remove(listener)
            self._sync_focus_handler()

    def _sync_focus_handler(self) -> None:
        self._focus_generation += 1
        epoch, generation, backend = (
            self._focus_generation,
            self._generation,
            self._backend,
        )

        if backend is None:
            return

        def receive(request: TrayFocusRequest) -> None:
            GLib.idle_add(self._deliver_focus, request, epoch, generation, backend)

        backend.set_focus_handler(
            receive if self._focus_listeners and self._backend is not None else None
        )

    def _deliver_focus(
        self,
        request: TrayFocusRequest,
        epoch: int,
        generation: int,
        backend: TrayBackend,
    ) -> bool:
        def current() -> bool:
            return (
                self._running
                and self._backend is not None
                and epoch == self._focus_generation
                and generation == self._generation
                and backend is self._backend
            )

        if current():
            guarded = TrayFocusRequest(
                request.identifier,
                lambda: current() and request.claim(),
                lambda: current() and request.validate(),
            )
            for listener in tuple(self._focus_listeners):
                if current():
                    listener(guarded)
        return False

    def set_geometry(self, bounds: Mapping[str, Rect]) -> None:
        backend = self._backend
        if self._backend is not None:
            backend.set_geometry(bounds)

    def pointer(
        self,
        identifier: str | None,
        *,
        point: tuple[int, int] | None = None,
        anchor: Rect | None = None,
        generation: int | None = None,
    ) -> bool:
        backend = self._backend
        return (
            backend is not None
            and self._command_current(generation)
            and backend.pointer(identifier, point=point, anchor=anchor)
        )

    def double_click(
        self,
        identifier: str,
        *,
        anchor: Rect | None = None,
        generation: int | None = None,
    ) -> bool:
        backend = self._backend
        return (
            backend is not None
            and self._command_current(generation)
            and (
                backend.double_click(identifier, anchor=anchor)
                if anchor is not None
                else backend.double_click(identifier)
            )
        )

    def _command_current(self, generation: int | None) -> bool:
        # Capture the backend before this check: a subsequent restart may
        # close that backend, but must never reroute old work to its replacement.
        return (
            self._backend is not None
            and self._ready_to_read.is_set()
            and (generation is None or generation == self._generation)
        )

    def start(self) -> None:
        """Poll immediately, then on every interval until stopped."""
        if self._running:
            return
        if self._backend is None:
            self._backend = self._new_backend()
            self._state = self._backend.initial_state()
        self._generation += 1
        self._running = True
        self._sync_focus_handler()
        self.refresh()
        generation = self._generation
        self._timer_id = GLib.timeout_add_seconds(
            self._interval_s, lambda: self._tick(generation)
        )

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
        self._focus_listeners.clear()
        self._backend.set_focus_handler(None)
        self._focus_generation += 1
        self._backend = None
        self._worker.stop()

    def refresh(self) -> None:
        """Request an immediate poll; a request in flight is not duplicated."""
        if not self._running:
            return
        backend = self._backend
        if backend is None:
            return
        generation, ready = self._generation, self._ready_to_read

        def read() -> TrayState:
            ready.wait()
            if generation != self._generation or not self._running:
                return backend.initial_state()
            return backend.get_state()

        self._worker.run_guarded(
            key=f"{_POLL_KEY}:{generation}",
            name="status-notifier-poll",
            fn=read,
            on_result=lambda state: (
                self._on_state_result(state)
                if generation == self._generation
                else False
            ),
            on_error=lambda exc: (
                self._on_poll_error(exc) if generation == self._generation else False
            ),
        )

    def activate(
        self,
        identifier: str,
        *,
        anchor: Rect | None = None,
        keyboard: bool = False,
        generation: int | None = None,
    ) -> bool:
        backend = self._backend
        return (
            backend is not None
            and self._command_current(generation)
            and backend.activate(identifier, anchor=anchor, keyboard=keyboard)
        )

    def context_menu(
        self,
        identifier: str,
        *,
        anchor: Rect | None = None,
        keyboard: bool = False,
        generation: int | None = None,
    ) -> bool:
        backend = self._backend
        return (
            backend is not None
            and self._command_current(generation)
            and backend.context_menu(identifier, anchor=anchor, keyboard=keyboard)
        )

    def menu_client(self, identifier: str) -> TrayMenuClient | None:
        return (
            self._backend.menu_client(identifier) if self._backend is not None else None
        )

    def _tick(self, generation: int | None = None) -> bool:
        if generation is not None and generation != self._generation:
            return False
        self.refresh()
        return self._running

    def _on_state_result(self, state: TrayState) -> bool:
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
