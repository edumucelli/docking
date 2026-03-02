"""Rendering helpers for Desktop applet."""

from __future__ import annotations

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from docking.applets.base import load_theme_icon


def create_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    """Return static user-desktop icon from theme."""
    return load_theme_icon(name="user-desktop", size=size)
