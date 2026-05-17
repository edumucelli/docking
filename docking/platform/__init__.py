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

"""Convenience exports for Docking's platform integration layer.

What the platform layer owns

The ``platform`` package contains the code that has to speak to the outside
desktop session: launchers, window tracking, environment quirks, struts,
barriers, dodge behavior, and model-building rules that depend on what the
window manager or desktop shell is doing.

Why this module exists

Like ``docking.core.__init__``, this file is a stable import surface. Higher
layers often only need the main platform-facing objects, not the entire module
layout. Re-exporting them here keeps call sites simpler without collapsing the
actual implementation into one file.

Why the boundary matters

This package is intentionally below the UI layer and above the pure core layer.
It may know about X11, desktop IDs, running windows, and monitor/workspace
state, but it should not become responsible for GTK widget trees or drawing.
"""

from docking.platform.launcher import Launcher  # noqa: F401
from docking.platform.model import DockItem, DockModel, LauncherEntryState  # noqa: F401
from docking.platform.unity import UnityLauncherListener  # noqa: F401
from docking.platform.window_tracker import WindowTracker  # noqa: F401
