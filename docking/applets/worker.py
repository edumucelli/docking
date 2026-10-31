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
from queue import Queue
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

        try:
            self.run(
                name=name,
                fn=fn,
                on_result=guarded_result,
                on_error=guarded_error,
            )
        except Exception:
            finish()
            raise
        return True


class SerialWorker(BackgroundWorker):
    """Keep related IO on one owned thread until stop, without blocking GTK.

    The finalizer runs on that thread after its last task, independently of GTK
    result delivery. Construction starts no thread. Call stop() to release an
    idle worker; its pending callbacks remain the caller's lifecycle to guard.
    """

    def __init__(self, *, on_close: Callable[[], None], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._on_close = on_close
        self._requests: Queue[Callable[[], None] | None] = Queue()
        self._queue_lock = threading.Lock()
        self._thread = None
        self._closed = False

    def run(
        self,
        *,
        name: str,
        fn: Callable[[], T],
        on_result: Callable[[T], Any] | None = None,
        on_error: Callable[[Exception], Any] | None = None,
    ) -> None:
        def request() -> None:
            try:
                result = fn()
            except Exception as exc:
                self.log.bind(action=name).debug("Background task failed: %s", exc)
                if on_error is not None:
                    self._idle_add(on_error, exc)
            else:
                if on_result is not None:
                    self._idle_add(on_result, result)

        with self._queue_lock:
            if self._closed:
                raise RuntimeError("Worker is stopped")
            self._requests.put(request)
            if self._thread is None:
                try:
                    self._thread = self._thread_factory(
                        target=self._serve, daemon=False
                    )
                    self._thread.start()
                except Exception:
                    self._thread = None
                    self._requests.get_nowait()
                    raise

    def _serve(self) -> None:
        try:
            while (request := self._requests.get()) is not None:
                request()
        finally:
            try:
                self._on_close()
            except Exception:
                self.log.exception("Worker cleanup failed")

    def stop(self) -> None:
        with self._queue_lock:
            if self._closed:
                return
            self._closed = True
            self._requests.put(None)
