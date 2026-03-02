"""Pure rendering helpers for Battery applet."""

from __future__ import annotations

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from docking.applets.base import load_theme_icon_centered
from docking.applets.battery.state import BatteryState, icon_name_for


def render_icon(size: int, state: BatteryState | None) -> GdkPixbuf.Pixbuf | None:
    """Render battery icon from current state via theme icon lookup."""
    return load_theme_icon_centered(name=icon_name_for(state=state), size=size)
