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

"""Docking's platform integration layer.

What the platform layer owns

The ``platform`` package contains the code that has to speak to the outside
desktop session: launchers, window tracking, environment quirks, struts,
barriers, dodge behavior, and model-building rules that depend on what the
window manager or desktop shell is doing.

Why this module is a no-op

Callers import directly from the submodules (``docking.platform.model``,
``docking.platform.targets``, etc.). Eager re-exports here previously created a
cycle when low-level modules (e.g. ``docking.platform.environment.xdg``) were
needed by ``docking.core.config`` -- loading the parent package triggered
higher-level platform services which import ``config``, creating an import cycle.

Why the boundary matters

This package is intentionally below the UI layer and above the pure core layer.
It may know about X11, desktop IDs, running windows, and monitor/workspace
state, but it should not become responsible for GTK widget trees or drawing.
"""
