"""Coalesce native Start Menu and AppsFolder changes into the shared registry."""

import ctypes
import os
import time
from collections.abc import Callable
from contextlib import ExitStack
from threading import RLock

from gi.repository import GLib

from .ffi import explorer_tray
from .shell_changes import ShellChangeMonitor


class CatalogChangeMonitor:
    def __init__(
        self,
        changed: Callable[[], None],
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._changed = changed
        self._clock = clock
        self._lock = RLock()
        self._read_serial = 0
        self._reads: set[int] = set()
        self._settle_deadline = 0.0
        self._retry_at: float | None = None
        self._subscriptions = ExitStack()
        self._folders: list[ShellChangeMonitor] = []
        self._closed = False
        self._source = 0
        self._poll_source = 0
        self._user = ctypes.WinDLL("user32", use_last_error=True)
        self._shell = explorer_tray(self._user, exclude_pid=os.getpid())
        try:
            for folder in (
                "a77f5d77-2e2b-44c3-a6a2-aba601054a51",  # Programs
                "0139d44e-6afe-49f2-8690-3dafcae6ffb8",  # CommonPrograms
                "1e87508d-89c2-42f0-8a7e-645a0f50ca58",  # AppsFolder
            ):
                subscription = ShellChangeMonitor(
                    self._queue,
                    folder_id=folder,
                    title="Docking application catalog monitor",
                )
                self._folders.append(subscription)
                self._subscriptions.callback(subscription.close)
            # A shell replacement can leave an in-flight read and native
            # subscriptions stale even when TaskbarCreated was not delivered.
            # Observe its lifetime, rather than polling the application catalog.
            self._poll_source = GLib.timeout_add(500, self._check_shell)
        except BaseException:
            self.close()
            raise

    def _check_shell(self) -> bool:
        if self._closed:
            return False
        self._observe_shell()
        self._reconcile()
        return not self._closed

    def _observe_shell(self) -> None:
        shell = explorer_tray(self._user, exclude_pid=os.getpid())
        if shell != self._shell:
            self._shell = shell
            self._settle()
            for subscription in self._folders:
                subscription.reconnect()

    def _queue(self) -> None:
        if not self._closed:
            # One folder may receive the broadcast before the other observers.
            # Reconnect all of them before coalescing the catalog invalidation.
            self._observe_shell()
            self._settle()
            if not self._source:
                self._source = GLib.idle_add(self._notify)

    def _settle(self) -> None:
        with self._lock:
            # AppsFolder can successfully enumerate an old snapshot after a
            # real change. Reconcile within the normal refresh horizon, but
            # only after complete resolver calls, never by cancelling them.
            self._settle_deadline = self._clock() + 30.0
            self._retry_at = None

    def read_started(self) -> int | None:
        """Reserve activity before the worker checks its cancellation token."""
        with self._lock:
            if self._closed:
                return None
            self._read_serial += 1
            self._reads.add(self._read_serial)
            self._retry_at = None
            return self._read_serial

    def read_finished(self, ticket: int | None) -> None:
        """Unchanged successful reads can still precede Shell cache convergence."""
        with self._lock:
            if self._closed or ticket is None:
                return
            self._reads.remove(ticket)
            if not self._reads and self._clock() < self._settle_deadline:
                self._retry_at = self._clock() + 1.0

    def _reconcile(self) -> None:
        with self._lock:
            now = self._clock()
            if now >= self._settle_deadline:
                self._retry_at = None
                return
            if (
                self._closed
                or self._reads
                or self._source
                or self._retry_at is None
                or now < self._retry_at
            ):
                return
            self._retry_at = None
            # Keep the activity gate until invalidation is scheduled. A new
            # resolver cannot begin between the idle check and this callback;
            # real native changes still invalidate immediately through _notify.
            self._changed()

    def _notify(self) -> bool:
        self._source = 0
        if not self._closed:
            self._changed()
        return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._reads.clear()
            self._retry_at = None
            self._settle_deadline = 0.0
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0
        self._subscriptions.close()
        self._folders.clear()
