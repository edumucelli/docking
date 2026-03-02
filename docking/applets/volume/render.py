"""Rendering helpers for volume applet."""

from __future__ import annotations

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from docking.applets.base import load_theme_icon

from .state import _volume_icon_name


def create_volume_icon(
    *, size: int, volume: int, muted: bool
) -> GdkPixbuf.Pixbuf | None:
    icon = _volume_icon_name(volume=volume, muted=muted)
    return load_theme_icon(name=icon, size=size)
