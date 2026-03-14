"""GTK lifecycle glue for Clippy applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk

from docking.applets.base import Applet
from docking.applets.clippy.render import create_icon
from docking.applets.clippy.state import (
    _truncate,
    add_clip,
    cycle_position,
    tooltip_text,
)
from docking.applets.identity import AppletId
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config


class ClippyApplet(Applet):
    """Clipboard history applet. Scroll to cycle, click to paste, menu to pick."""

    id = AppletId.CLIPPY
    name = _("Clippy")
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

        super().__init__(icon_size=icon_size, config=config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Static edit-paste icon."""
        return create_icon(size=size)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(
            clips=self._clips,
            cur_position=self._cur_position,
        )

    def on_clicked(self) -> None:
        """Copy current clip back to clipboard."""
        if self._clips and 0 < self._cur_position <= len(self._clips):
            text = self._clips[self._cur_position - 1]
            if self._clipboard:
                self._clipboard.set_text(text, -1)
                self._clipboard.store()

    def on_scroll(self, direction_up: bool) -> None:
        """Cycle through clipboard history."""
        self._cur_position = cycle_position(
            clips_len=len(self._clips),
            cur_position=self._cur_position,
            direction_up=direction_up,
        )
        if self._cur_position:
            self.refresh_presentation()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """List all clips (newest first) + Clear button."""
        items: list[Gtk.MenuItem] = []

        for clip in reversed(self._clips):
            menu_item = Gtk.MenuItem(label=_truncate(clip))
            menu_item.connect(
                "activate",
                lambda _, t=clip: self._copy_to_clipboard(text=t),
            )
            items.append(menu_item)

        if self._clips:
            items.append(Gtk.SeparatorMenuItem())
            clear = Gtk.MenuItem(label=_("Clear"))
            clear.connect("activate", lambda _: self._clear())
            items.append(clear)

        return items

    def start(self, notify: Callable[[], None]) -> None:
        """Connect to clipboard owner-change signal."""
        super().start(notify=notify)
        self._clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self._handler_id = self._clipboard.connect(
            "owner-change",
            self._on_owner_change,
        )

    def stop(self) -> None:
        """Disconnect clipboard signal."""
        if self._clipboard and self._handler_id:
            self._clipboard.disconnect(self._handler_id)
            self._handler_id = 0
        self._clipboard = None
        super().stop()

    def _on_owner_change(self, clipboard: Gtk.Clipboard, _event: object) -> None:
        """Clipboard content changed; grab text and add to history."""
        text = clipboard.wait_for_text()
        if not text:
            return
        self.add_clip(text=text)
        self.refresh_presentation()

    def add_clip(self, text: str) -> None:
        """Add a clip to history (dedup, cap at max_entries)."""
        self._clips, self._cur_position = add_clip(
            clips=self._clips,
            text=text,
            max_entries=self._max_entries,
        )

    def _copy_to_clipboard(self, text: str) -> None:
        """Copy text to system clipboard."""
        if self._clipboard:
            self._clipboard.set_text(text, -1)
            self._clipboard.store()

    def _clear(self) -> None:
        """Clear all clipboard history."""
        self._clips.clear()
        self._cur_position = 0
        self.refresh_presentation()
