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

"""Startup popup arbitration."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

import gi

gi.require_version("GLib", "2.0")
from gi.repository import GLib


class StartupPopupSource(Protocol):
    """Source that can request a startup popup without knowing the coordinator."""

    source_id: str
    priority: int
    max_wait_seconds: int | None

    def start(self, request_show, visibility_changed) -> None:
        """Start source-specific startup work."""

    def stop(self) -> None:
        """Stop source-specific work."""

    def show_pending(self) -> bool:
        """Show pending content and return whether a popup became visible."""


@dataclass(slots=True)
class _PendingRequest:
    requested_at: float
    expire_source_id: int = 0


class StartupPopupCoordinator:
    """Own startup popup priority and source lifecycle."""

    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._sources: dict[str, StartupPopupSource] = {}
        self._pending: dict[str, _PendingRequest] = {}
        self._active_source_id = ""

    def register(self, source: StartupPopupSource) -> None:
        self._sources[source.source_id] = source

    def start(self) -> None:
        for source in sorted(self._sources.values(), key=lambda item: item.priority):
            source.start(self.request_show, self.visibility_changed)

    def stop(self) -> None:
        for request in self._pending.values():
            if request.expire_source_id:
                GLib.source_remove(request.expire_source_id)
        self._pending.clear()
        self._active_source_id = ""
        for source in self._sources.values():
            source.stop()

    def request_show(self, source_id: str) -> None:
        source = self._sources.get(source_id)
        if source is None:
            return
        if source_id not in self._pending:
            self._pending[source_id] = _PendingRequest(requested_at=self._clock())
            self._schedule_expiry(source=source)
        self._try_show_next()

    def visibility_changed(self, source_id: str, visible: bool) -> None:
        if visible:
            self._active_source_id = source_id
            self._clear_pending(source_id)
            return
        if self._active_source_id == source_id:
            self._active_source_id = ""
        self._try_show_next()

    def _try_show_next(self) -> None:
        if self._active_source_id:
            return
        self._drop_expired_requests()
        while self._pending:
            source = min(
                (self._sources[source_id] for source_id in self._pending),
                key=lambda item: item.priority,
            )
            self._clear_pending(source.source_id)
            if source.show_pending():
                self._active_source_id = source.source_id
                return

    def _schedule_expiry(self, *, source: StartupPopupSource) -> None:
        max_wait_seconds = source.max_wait_seconds
        if max_wait_seconds is None:
            return
        request = self._pending[source.source_id]
        request.expire_source_id = GLib.timeout_add_seconds(
            max_wait_seconds,
            self._expire_source,
            source.source_id,
        )

    def _expire_source(self, source_id: str) -> bool:
        request = self._pending.get(source_id)
        if request is not None:
            request.expire_source_id = 0
        self._clear_pending(source_id)
        return False

    def _drop_expired_requests(self) -> None:
        for source_id, request in tuple(self._pending.items()):
            source = self._sources.get(source_id)
            if source is None or source.max_wait_seconds is None:
                continue
            if self._clock() - request.requested_at >= source.max_wait_seconds:
                self._clear_pending(source_id)

    def _clear_pending(self, source_id: str) -> None:
        request = self._pending.pop(source_id, None)
        if request is not None and request.expire_source_id:
            GLib.source_remove(request.expire_source_id)
