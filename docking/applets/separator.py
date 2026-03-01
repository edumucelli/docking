"""Separator applet -- transparent gap between dock items.

Supports multiple instances via unique desktop_ids (e.g. applet://separator#0).
Size adjustable via scroll or right-click menu.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from docking.applets.base import Applet
from docking.applets.ids import AppletId

if TYPE_CHECKING:
    from docking.core.config import Config

DEFAULT_SIZE = 5
MIN_SIZE = 2
MAX_SIZE = 48
STEP = 2


class SeparatorApplet(Applet):
    """A thin transparent gap that can be inserted multiple times.

    Renders as empty space between dock items.
    Scroll or right-click to adjust width.
    """

    id = AppletId.SEPARATOR
    name = "Separator"
    icon_name = "list-remove"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._gap = DEFAULT_SIZE
        super().__init__(icon_size, config)
        self.item.main_size = self._gap

    def _prefs_key(self) -> str:
        """Per-instance prefs key (e.g. 'separator#0')."""
        return self.item.desktop_id.removeprefix("applet://")

    def load_instance_prefs(self) -> dict[str, Any]:
        if self._config:
            return dict(self._config.applet_prefs.get(self._prefs_key(), {}))
        return {}

    def save_instance_prefs(self, prefs: dict[str, Any]) -> None:
        if self._config:
            self._config.applet_prefs[self._prefs_key()] = prefs
            self._config.save()

    def apply_prefs(self) -> None:
        """Load persisted gap size after desktop_id is finalized."""
        prefs = self.load_instance_prefs()
        self._gap = prefs.get("gap", DEFAULT_SIZE)
        self.item.main_size = self._gap
        self.refresh_icon()

    def _set_gap(self, gap: int) -> None:
        self._gap = max(MIN_SIZE, min(MAX_SIZE, gap))
        self.item.main_size = self._gap
        self.save_instance_prefs(prefs={"gap": self._gap})
        self.refresh_icon()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        w = max(MIN_SIZE, self._gap)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, size)
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, size)

    def on_scroll(self, direction_up: bool) -> None:
        """Scroll to adjust gap width."""
        self._set_gap(gap=self._gap + STEP if direction_up else self._gap - STEP)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        increase = Gtk.MenuItem(label="Increase Gap")
        increase.connect("activate", lambda _: self._set_gap(gap=self._gap + STEP))
        decrease = Gtk.MenuItem(label="Decrease Gap")
        decrease.connect("activate", lambda _: self._set_gap(gap=self._gap - STEP))
        return [increase, decrease]
