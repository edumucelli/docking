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
from docking.platform.model import DockItem, DockModel  # noqa: F401
from docking.platform.window_tracker import WindowTracker  # noqa: F401
