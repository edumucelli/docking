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

"""Small backend services exposed to applets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docking.platform.backends.base import (
        DesktopActionService,
        IdleService,
        ScreenCaptureService,
        WindowPickService,
        WorkspaceService,
    )


@dataclass(frozen=True)
class AppletServices:
    """Optional backend services consumed by platform-sensitive applets."""

    desktop_actions: DesktopActionService | None = None
    workspaces: WorkspaceService | None = None
    window_picker: WindowPickService | None = None
    idle: IdleService | None = None
    screen_capture: ScreenCaptureService | None = None
