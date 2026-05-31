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

"""X11 runtime construction helpers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from docking.log import get_logger
from docking.platform.backends.x11.windows import X11WindowService
from docking.platform.window_tracker import WindowTracker

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel


X11_WINDOW_SERVICE_ENV = "DOCKING_X11_WINDOW_SERVICE"
X11_WINDOW_SERVICE_LEGACY = "legacy"
X11_WINDOW_SERVICE_SERVICE = "service"

log = get_logger(name="x11_session")


def build_x11_window_tracker(
    *, model: DockModel, launcher: Launcher, config: Config | None = None
) -> WindowTracker:
    """Build the X11 window runtime used by current UI compatibility callers."""
    mode = _window_service_mode()
    if mode == X11_WINDOW_SERVICE_LEGACY:
        return WindowTracker(model=model, launcher=launcher, config=config)
    return X11WindowService(model=model, launcher=launcher, config=config)


def _window_service_mode() -> str:
    raw_mode = os.environ.get(X11_WINDOW_SERVICE_ENV, X11_WINDOW_SERVICE_SERVICE)
    mode = raw_mode.strip().lower()
    if mode in {X11_WINDOW_SERVICE_LEGACY, X11_WINDOW_SERVICE_SERVICE}:
        return mode
    log.warning(
        "Ignoring invalid %s=%r; using %s",
        X11_WINDOW_SERVICE_ENV,
        raw_mode,
        X11_WINDOW_SERVICE_SERVICE,
    )
    return X11_WINDOW_SERVICE_SERVICE
