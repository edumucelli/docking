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

"""X11 idle-time service."""

from __future__ import annotations

from docking.platform.backends.base import IdleService
from docking.platform.backends.x11.impl.idle_time import _get_idle_ms


class X11IdleService(IdleService):
    """IdleService backed by XScreenSaver idle milliseconds."""

    def start(self) -> None:
        """No service-level runtime loop is needed."""

    def stop(self) -> None:
        """No persistent resources are held."""

    def idle_seconds(self) -> float | None:
        idle_ms = _get_idle_ms()
        if idle_ms is None:
            return None
        return idle_ms / 1000.0
