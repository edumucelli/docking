"""Shared dock item data structures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class DockItem:
    """A single item in the dock."""

    desktop_id: str
    name: str = ""
    icon_name: str = "application-x-executable"
    wm_class: str = ""
    is_pinned: bool = False
    is_running: bool = False
    is_active: bool = False
    is_urgent: bool = False
    instance_count: int = 0
    # Icon object (typically GdkPixbuf.Pixbuf), kept generic to avoid GTK coupling.
    icon: Any | None = None
    # Custom slot width along main axis (0 = use icon_size)
    main_size: int = 0
    # Timestamps for animations (monotonic microseconds, 0 = inactive)
    last_clicked: int = 0
    last_launched: int = 0
    last_urgent: int = 0
    # Callable returning tooltip widget/content; used by applets for rich tooltips
    tooltip_builder: Callable[[], Any] | None = None
