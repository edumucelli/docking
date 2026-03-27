"""Shared dock item data structures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

ItemKind = Literal["app", "applet", "file", "folder"]

APP_KIND: ItemKind = "app"
APPLET_KIND: ItemKind = "applet"
FILE_KIND: ItemKind = "file"
FOLDER_KIND: ItemKind = "folder"


@dataclass
class DockItem:
    """A single item in the dock."""

    desktop_id: str
    kind: ItemKind = APP_KIND
    # Canonical launch/open target. Apps keep desktop_id here; files/folders
    # use normalized file:// URIs so identity remains stable across the UI.
    target: str = ""
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
    # Optional key for per-item preferences (folder sort/view options, etc.).
    prefs_key: str = ""
    # Some items, like separators, should keep a fixed size under hover.
    allow_zoom: bool = True
    # Insert/remove animation factor: 0.0 = fully collapsed, 1.0 = fully visible.
    # Layout scales the item's effective width by this factor.
    insert_factor: float = 1.0
    # Former visible index used to keep shrink-out animations in place instead of
    # appending them at the end of the dock.
    removal_index: int = -1

    def __post_init__(self) -> None:
        if not self.target:
            self.target = self.desktop_id
        if not self.prefs_key:
            self.prefs_key = self.target
