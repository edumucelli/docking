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

"""Shared dock item data structures."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

ItemKind = Literal["app", "applet", "file", "folder"]

APP_KIND: ItemKind = "app"
APPLET_KIND: ItemKind = "applet"
FILE_KIND: ItemKind = "file"
FOLDER_KIND: ItemKind = "folder"


class IconPixmapLike(Protocol):
    """Pixmap-like icon contract used by the renderer and color helpers."""

    def get_width(self) -> int: ...

    def get_height(self) -> int: ...

    def get_pixels(self) -> object: ...

    def get_n_channels(self) -> int: ...

    def get_rowstride(self) -> int: ...


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
    window_urgent: bool = False
    instance_count: int = 0
    # Icon object (typically GdkPixbuf.Pixbuf), typed structurally to avoid runtime
    # GTK coupling while keeping test doubles valid.
    icon: IconPixmapLike | None = None
    # Custom slot width along main axis (0 = use icon_size)
    main_size: int = 0
    # Timestamps for animations (monotonic microseconds, 0 = inactive)
    last_clicked: int = 0
    last_launched: int = 0
    last_urgent: int = 0
    # Unix timestamp (wall-clock seconds) of last window close, for tooltip display
    last_closed: float = 0
    badge_count: int = 0
    badge_visible: bool = False
    progress: float = 0.0
    progress_visible: bool = False
    launcher_entry_urgent: bool = False
    # Callable returning tooltip widget/content; used by applets for rich tooltips
    tooltip_builder: Callable[[], Any] | None = None
    # Optional key for per-item preferences (folder sort/view options, etc.).
    prefs_key: str = ""
    # Some items, like separators, should keep a fixed size under hover.
    allow_zoom: bool = True
    # Whether this item is in the recent-apps section (not pinned, not running,
    # but recently used). The renderer reads this to apply reduced opacity.
    is_recent: bool = False
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
