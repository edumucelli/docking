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

from typing import TYPE_CHECKING

from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.backends.x11.session import X11SessionBackend
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel

log = get_logger(name="backend_selection")


def create_session_backend(
    *, config: Config, launcher: Launcher, model: DockModel
) -> X11SessionBackend:
    """Create the production session backend for the current runtime.

    Native Wayland and reduced backends are not selected yet. Import the X11
    backend lazily so future native Wayland startup can avoid importing X11-only
    modules before selection.
    """
    from docking.platform.backends.x11.session import X11SessionBackend

    backend = X11SessionBackend(
        model=model,
        launcher=launcher,
        config=config,
    )
    log.info("Selected session backend: %s", backend.name)
    return backend
