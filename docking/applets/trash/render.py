"""Rendering helpers for Trash applet."""

from __future__ import annotations

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf  # noqa: E402

from docking.applets.base import load_theme_icon


def trash_icon_name(*, item_count: int) -> str:
    return "user-trash-full" if item_count > 0 else "user-trash"


def trash_tooltip(*, item_count: int) -> str:
    if item_count == 0:
        return "No items in Trash"
    if item_count == 1:
        return "1 item in Trash"
    return f"{item_count} items in Trash"


def create_trash_icon(*, size: int, item_count: int) -> GdkPixbuf.Pixbuf | None:
    return load_theme_icon(name=trash_icon_name(item_count=item_count), size=size)
