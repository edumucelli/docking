"""Coalesce native Start Menu and AppsFolder changes into the shared registry."""

import ctypes
import os
from collections.abc import Callable
from contextlib import ExitStack

from gi.repository import GLib

from .ffi import explorer_tray
from .shell_changes import ShellChangeMonitor


class CatalogChangeMonitor:
    def __init__(self, changed: Callable[[], None]) -> None:
        self._changed = changed
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
        return True

    def _observe_shell(self) -> None:
        shell = explorer_tray(self._user, exclude_pid=os.getpid())
        if shell != self._shell:
            self._shell = shell
            for subscription in self._folders:
                subscription.reconnect()

    def _queue(self) -> None:
        if not self._closed:
            # One folder may receive the broadcast before the other observers.
            # Reconnect all of them before coalescing the catalog invalidation.
            self._observe_shell()
            if not self._source:
                self._source = GLib.idle_add(self._notify)

    def _notify(self) -> bool:
        self._source = 0
        if not self._closed:
            self._changed()
        return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._poll_source:
            GLib.source_remove(self._poll_source)
            self._poll_source = 0
        if self._source:
            GLib.source_remove(self._source)
            self._source = 0
        self._subscriptions.close()
        self._folders.clear()
