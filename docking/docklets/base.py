"""Docklet base class -- special-purpose dock items with custom rendering."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

if TYPE_CHECKING:
    from docking.core.config import Config

DOCKLET_PREFIX = "docklet://"


def load_theme_icon(name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load an icon by name from the default GTK icon theme."""
    theme = Gtk.IconTheme.get_default()
    try:
        return theme.load_icon(name, size, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        return None


def load_theme_icon_centered(name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load icon from theme, centered on a square canvas if non-square.

    Useful for icons that are taller than wide (e.g. battery icons).
    """
    raw = load_theme_icon(name, size)
    if raw is None:
        return None
    w, h = raw.get_width(), raw.get_height()
    if w == h:
        return raw
    canvas = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, size, size)
    canvas.fill(0x00000000)
    x = (size - w) // 2
    y = (size - h) // 2
    raw.composite(
        canvas, x, y, w, h, x, y, 1.0, 1.0, GdkPixbuf.InterpType.BILINEAR, 255
    )
    return canvas


def is_docklet(desktop_id: str) -> bool:
    """True if desktop_id refers to a docklet rather than a .desktop app."""
    return desktop_id.startswith(DOCKLET_PREFIX)


def docklet_id_from(desktop_id: str) -> str:
    """Extract docklet id from desktop_id (e.g. 'docklet://clock' -> 'clock')."""
    return desktop_id[len(DOCKLET_PREFIX) :]


class Docklet(ABC):
    """Base class for dock plugins that render custom icons.

    Each docklet owns a DockItem. The docklet renders custom Cairo
    content to a pixbuf and assigns it to item.icon. The existing
    renderer draws it like any other icon -- no renderer changes needed.

    Lifecycle:
      __init__  -> create item + initial icon
      start()   -> begin timers/monitors (called after dock is ready)
      stop()    -> cleanup (called on removal or shutdown)
    """

    id: str
    name: str
    icon_name: str

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        # Deferred import: DockItem imports from this module (circular)
        from docking.platform.model import DockItem

        self._config = config
        self.item = DockItem(
            desktop_id=self.desktop_id,
            name=self.name,
            icon_name=self.icon_name,
            is_pinned=True,
            icon=self.create_icon(icon_size),
        )
        self._icon_size = icon_size
        self._notify: Callable[[], None] | None = None

    @property
    def desktop_id(self) -> str:
        return f"{DOCKLET_PREFIX}{self.id}"

    def load_prefs(self) -> dict[str, Any]:
        """Load this docklet's preferences from config."""
        if self._config:
            return dict(self._config.docklet_prefs.get(self.id, {}))
        return {}

    def save_prefs(self, prefs: dict[str, Any]) -> None:
        """Save this docklet's preferences to config."""
        if self._config:
            self._config.docklet_prefs[self.id] = prefs
            self._config.save()

    @abstractmethod
    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render custom content to a pixbuf at the given size."""

    def on_clicked(self) -> None:
        """Handle left-click (default: no-op)."""

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Extra right-click menu items (default: empty)."""
        return []

    def start(self, notify: Callable[[], None]) -> None:
        """Start timers/monitors. Call notify() to trigger redraw."""
        self._notify = notify

    def stop(self) -> None:
        """Cleanup timers/monitors."""
        self._notify = None

    def refresh_icon(self) -> None:
        """Re-render the icon and trigger a redraw."""
        self.item.icon = self.create_icon(self._icon_size)
        if self._notify:
            self._notify()
