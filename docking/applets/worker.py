"""Shared background worker helpers for GTK-bound code.

This module standardizes the common Docking pattern:

1. Run slow or blocking work off the GTK main thread.
2. Marshal the result back onto the main loop with ``GLib.idle_add``.
3. Optionally guard requests by key so repeated triggers do not launch the
   same task multiple times while one is already in flight.

The goal is pragmatic consistency, not a large scheduler. Most applets only
need two modes:

- ``run(...)`` for plain background work
- ``run_guarded(...)`` when duplicate requests should be ignored until the
  current one completes on the main loop

GTK mutation must stay on the main loop. Callers therefore provide callbacks
that are safe to run from ``GLib.idle_add``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

from gi.repository import GLib

from docking.log import DockingContextAdapter, get_logger, with_context

T = TypeVar("T")


class BackgroundWorker:
    """Run background tasks and deliver results back on the GTK main loop."""

    def __init__(
        self,
        *,
        idle_add: Callable[..., Any] = GLib.idle_add,
        thread_factory: Callable[..., Any] = threading.Thread,
        logger: DockingContextAdapter | None = None,
    ) -> None:
        self._idle_add = idle_add
        self._thread_factory = thread_factory
        self.log = logger or with_context(get_logger("worker"))
        self._active_keys: set[str] = set()
        self._lock = threading.Lock()

    def run(
        self,
        *,
        name: str,
        fn: Callable[[], T],
        on_result: Callable[[T], Any] | None = None,
        on_error: Callable[[Exception], Any] | None = None,
    ) -> None:
        log = self.log.bind(action=name)

        def worker() -> None:
            try:
                result = fn()
            except Exception as exc:
                log.debug("Background task failed: %s", exc)
                if on_error is not None:
                    self._idle_add(on_error, exc)
                return
            if on_result is not None:
                self._idle_add(on_result, result)

        self._thread_factory(target=worker, daemon=True).start()

    def run_guarded(
        self,
        *,
        key: str,
        name: str,
        fn: Callable[[], T],
        on_result: Callable[[T], Any] | None = None,
        on_error: Callable[[Exception], Any] | None = None,
    ) -> bool:
        with self._lock:
            if key in self._active_keys:
                return False
            self._active_keys.add(key)

        def finish() -> bool:
            with self._lock:
                self._active_keys.discard(key)
            return False

        def guarded_result(result: T) -> bool:
            finish()
            if on_result is not None:
                on_result(result)
            return False

        def guarded_error(exc: Exception) -> bool:
            finish()
            if on_error is not None:
                on_error(exc)
            return False

        def task() -> None:
            try:
                result = fn()
            except Exception as exc:
                self.log.bind(action=name).debug("Background task failed: %s", exc)
                self._idle_add(guarded_error, exc)
                return
            if on_result is None:
                self._idle_add(finish)
                return
            self._idle_add(guarded_result, result)

        self._thread_factory(target=task, daemon=True).start()
        return True
