# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Approximate Wayland idle-time service backed by ext-idle-notify-v1."""

from __future__ import annotations

import time
from collections.abc import Callable

from docking.platform.backends.base import IdleService


class WaylandIdleService(IdleService):
    """Estimate idle seconds from ext-idle-notify-v1 resumed events.

    The protocol is threshold/event based rather than a direct idle-duration
    query. Docking uses a zero-time input idle notification and treats every
    resumed event as user activity, so ``idle_seconds()`` is best-effort.
    """

    def __init__(
        self,
        *,
        protocol: object,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._protocol = protocol
        self._clock = clock
        self._started = False
        self._last_activity = 0.0
        self._known = False

    def start(self) -> None:
        self._started = True
        self._last_activity = self._clock()
        start = getattr(self._protocol, "start", None)
        if callable(start):
            start(self)

    def stop(self) -> None:
        stop = getattr(self._protocol, "stop", None)
        if callable(stop):
            stop()
        self._started = False
        self._last_activity = 0.0
        self._known = False

    def idle_seconds(self) -> float | None:
        if not self._started or not self._known:
            return None
        return max(0.0, self._clock() - self._last_activity)

    def idled(self) -> None:
        """Called by the protocol adapter when the seat is idle."""
        self._known = True

    def resumed(self) -> None:
        """Called by the protocol adapter when user activity resumes."""
        self._last_activity = self._clock()
        self._known = True
