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

"""X11 backend services."""

from docking.platform.backends.x11.services.actions import WnckDesktopActionService
from docking.platform.backends.x11.services.capture import X11ScreenCaptureService
from docking.platform.backends.x11.services.idle import X11IdleService
from docking.platform.backends.x11.services.picking import WnckWindowPickService
from docking.platform.backends.x11.services.previews import X11PreviewService
from docking.platform.backends.x11.services.surface import X11SurfaceService
from docking.platform.backends.x11.services.visibility import X11VisibilityService
from docking.platform.backends.x11.services.windows import X11WindowService
from docking.platform.backends.x11.services.workspaces import WnckWorkspaceService
from docking.platform.backends.x11.session import (
    X11RuntimeServices,
    X11SessionBackend,
)

__all__ = [
    "WnckDesktopActionService",
    "WnckWindowPickService",
    "WnckWorkspaceService",
    "X11IdleService",
    "X11PreviewService",
    "X11RuntimeServices",
    "X11ScreenCaptureService",
    "X11SessionBackend",
    "X11SurfaceService",
    "X11VisibilityService",
    "X11WindowService",
]
