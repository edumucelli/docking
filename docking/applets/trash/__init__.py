"""Trash applet package."""

from .applet import TrashApplet
from .state import _count_trash_items

__all__ = [
    "TrashApplet",
    "_count_trash_items",
]
