"""Clippy applet -- clipboard history with scroll cycling.

Monitors the system clipboard (CLIPBOARD selection) for text changes.
Stores up to max_entries clips in memory (newest at end). Scroll cycles
through history; click copies current selection back to clipboard.
Right-click menu lists all clips for quick access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.applets.identity import AppletId

if TYPE_CHECKING:
    from docking.core.config import Config

MAX_DISPLAY_LEN = 50


def _truncate(text: str, max_len: int = MAX_DISPLAY_LEN) -> str:
    """Truncate text for menu display, replacing newlines with spaces."""
    clean = text.replace("\n", " ").replace("\t", " ").strip()
    if len(clean) > max_len:
        return clean[:max_len] + "..."
    return clean


class ClippyApplet(Applet):
    """Clipboard history applet. Scroll to cycle, click to paste, menu to pick."""

    id = AppletId.CLIPPY
    name = "Clippy"
    icon_name = "edit-paste"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._clips: list[str] = []
        self._cur_position: int = 0
        self._handler_id: int = 0
        self._clipboard: Gtk.Clipboard | None = None

        # Load prefs
        self._max_entries = 15
        if config:
            prefs = config.applet_prefs.get("clippy", {})
            self._max_entries = prefs.get("max_entries", 15)

        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Static edit-paste icon; tooltip shows current clip."""
        if hasattr(self, "item"):
            if self._clips and 0 < self._cur_position <= len(self._clips):
                self.item.name = _truncate(self._clips[self._cur_position - 1])
            else:
                self.item.name = "Clippy (empty)"
        return load_theme_icon(name="edit-paste", size=size)

    def on_clicked(self) -> None:
        """Copy current clip back to clipboard."""
        if self._clips and 0 < self._cur_position <= len(self._clips):
            text = self._clips[self._cur_position - 1]
            if self._clipboard:
                self._clipboard.set_text(text, -1)
                self._clipboard.store()

    def on_scroll(self, direction_up: bool) -> None:
        """Cycle through clipboard history."""
        if not self._clips:
            return
        if direction_up:
            self._cur_position -= 1
            if self._cur_position < 1:
                self._cur_position = len(self._clips)
        else:
            self._cur_position += 1
            if self._cur_position > len(self._clips):
                self._cur_position = 1
        self.refresh_icon()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """List all clips (newest first) + Clear button."""
        items: list[Gtk.MenuItem] = []

        for clip in reversed(self._clips):
            mi = Gtk.MenuItem(label=_truncate(clip))
            mi.connect(
                "activate",
                lambda _, t=clip: self._copy_to_clipboard(text=t),
            )
            items.append(mi)

        if self._clips:
            items.append(Gtk.SeparatorMenuItem())
            clear = Gtk.MenuItem(label="Clear")
            clear.connect("activate", lambda _: self._clear())
            items.append(clear)

        return items

    def start(self, notify: Callable[[], None]) -> None:
        """Connect to clipboard owner-change signal."""
        super().start(notify)
        self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self._handler_id = self._clipboard.connect(
            "owner-change", self._on_owner_change
        )

    def stop(self) -> None:
        """Disconnect clipboard signal."""
        if self._clipboard and self._handler_id:
            self._clipboard.disconnect(self._handler_id)
            self._handler_id = 0
        self._clipboard = None
        super().stop()

    def _on_owner_change(self, clipboard: Gtk.Clipboard, event: object) -> None:
        """Clipboard content changed; grab text and add to history."""
        text = clipboard.wait_for_text()
        if not text:
            return
        self.add_clip(text=text)
        self.refresh_icon()

    def add_clip(self, text: str) -> None:
        """Add a clip to history (dedup, cap at max_entries)."""
        if text in self._clips:
            self._clips.remove(text)
        self._clips.append(text)
        while len(self._clips) > self._max_entries:
            self._clips.pop(0)
        self._cur_position = len(self._clips)

    def _copy_to_clipboard(self, text: str) -> None:
        """Copy text to system clipboard."""
        if self._clipboard:
            self._clipboard.set_text(text, -1)
            self._clipboard.store()

    def _clear(self) -> None:
        """Clear all clipboard history."""
        self._clips.clear()
        self._cur_position = 0
        self.refresh_icon()
