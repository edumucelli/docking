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

"""Session backend selection.

This module is intentionally small while production still selects only the X11
backend. Keeping the selection point explicit now gives later native Wayland and
reduced-backend work a single place to add detection without threading display
checks through app startup or UI modules.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.platform.environment import (
    backend_name,
    is_wayland_session,
    is_x11_backend,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.base import SessionBackend
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel

log = get_logger(name="backend_selection")


def create_session_backend(
    *, config: Config, launcher: Launcher, model: DockModel
) -> SessionBackend:
    """Create the production session backend for the current runtime.

    Production still defaults to X11. ``DOCKING_BACKEND=reduced`` selects the
    reduced backend explicitly for validation without importing X11 services.
    """
    requested = os.environ.get("DOCKING_BACKEND", "").strip().lower()
    if requested == "reduced":
        return _create_reduced_backend(reason="requested by DOCKING_BACKEND=reduced")
    if requested == "x11":
        return _create_x11_backend(
            config=config,
            launcher=launcher,
            model=model,
            reason="requested by DOCKING_BACKEND=x11",
        )

    if not is_x11_backend():
        return _create_reduced_backend(reason=_non_x11_reason())

    return _create_x11_backend(
        config=config,
        launcher=launcher,
        model=model,
        reason="GTK display is X11",
    )


def _create_reduced_backend(*, reason: str) -> SessionBackend:
    from docking.platform.backends.reduced.session import ReducedSessionBackend

    backend = ReducedSessionBackend()
    log.info("Selected session backend: %s (%s)", backend.name, reason)
    return backend


def _create_x11_backend(
    *, config: Config, launcher: Launcher, model: DockModel, reason: str
) -> SessionBackend:
    from docking.platform.backends.x11.session import X11SessionBackend

    backend = X11SessionBackend(
        model=model,
        launcher=launcher,
        config=config,
    )
    log.info("Selected session backend: %s (%s)", backend.name, reason)
    return backend


def _non_x11_reason() -> str:
    session = "native Wayland" if is_wayland_session() else "non-X11"
    return f"{session} GTK backend: {backend_name()}"
