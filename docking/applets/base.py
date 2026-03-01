"""Applet base class -- special-purpose dock items with custom rendering."""

from __future__ import annotations

from abc import ABC, abstractmethod
from importlib import resources
from typing import TYPE_CHECKING, Any, Callable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from docking.applets.identity import (
    AppletId,
    applet_desktop_id,
    is_applet_desktop_id,
)
from docking.applets.identity import (
    applet_id_from as _applet_id_from,
)

if TYPE_CHECKING:
    from docking.core.config import Config

_ICON_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    # Not widely available outside GNOME/MATE icon packs.
    "applets-screenshooter": ("camera-photo", "camera-photo-symbolic"),
}

# Built-in applet icon names we guarantee via bundled fallback asset.
_BUNDLED_FALLBACK_ICON_NAMES = frozenset(
    {
        "alarm",
        "applets-screenshooter",
        "audio-speakers",
        "battery-good",
        "clock",
        "edit-paste",
        "list-remove",
        "office-calendar",
        "preferences-desktop-workspaces",
        "system-log-out",
        "user-desktop",
        "user-trash",
        "user-trash-full",
        "utilities-system-monitor",
        "view-app-grid",
    }
)
_BUNDLED_FALLBACK_ICON_PREFIXES = (
    "audio-volume-",
    "battery-",
    "network-",
    "weather-",
)


def _icon_name_candidates(name: str) -> tuple[str, ...]:
    names: list[str] = [name]
    if not name.endswith("-symbolic"):
        names.append(f"{name}-symbolic")
    names.extend(_ICON_NAME_ALIASES.get(name, ()))
    # Deduplicate while preserving order.
    return tuple(dict.fromkeys(names))


def _icon_theme_candidates() -> tuple[Gtk.IconTheme, ...]:
    themes: list[Gtk.IconTheme] = []

    default = Gtk.IconTheme.get_default()
    if default is not None:
        themes.append(default)

    # CI/headless sessions may not configure an icon theme in GtkSettings.
    for theme_name in ("Adwaita", "hicolor"):
        theme = Gtk.IconTheme()
        theme.set_custom_theme(theme_name)
        themes.append(theme)

    return tuple(themes)


def _should_use_bundled_fallback(name: str) -> bool:
    if name in _BUNDLED_FALLBACK_ICON_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in _BUNDLED_FALLBACK_ICON_PREFIXES)


def _load_bundled_fallback_icon(size: int) -> GdkPixbuf.Pixbuf | None:
    try:
        icon_ref = resources.files("docking.assets").joinpath(
            "icons/applet-fallback.png"
        )
        with resources.as_file(icon_ref) as icon_path:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(icon_path), size, size, True
            )
    except (FileNotFoundError, GLib.Error, ModuleNotFoundError):
        return None


def load_theme_icon(name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load an icon by name from the default GTK icon theme."""
    flags = Gtk.IconLookupFlags.FORCE_SIZE
    for icon_name in _icon_name_candidates(name=name):
        for theme in _icon_theme_candidates():
            try:
                return theme.load_icon(icon_name, size, flags)
            except GLib.Error:
                continue
    if _should_use_bundled_fallback(name=name):
        return _load_bundled_fallback_icon(size=size)
    return None


def load_theme_icon_centered(name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load icon from theme, centered on a square canvas if non-square.

    Useful for icons that are taller than wide (e.g. battery icons).
    """
    raw = load_theme_icon(name=name, size=size)
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


def draw_icon_label(cr: cairo.Context, text: str, size: int) -> None:
    """Draw outlined text at the bottom center of a size x size icon.

    Shared by weather (temperature), pomodoro (countdown), and hydration
    (countdown) for a uniform appearance.
    """
    font_size = max(1, int(size * 0.22))
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(Pango.FontDescription(f"Sans Bold {font_size}px"))
    layout.set_text(text, -1)
    _ink, logical = layout.get_pixel_extents()

    tx = (size - logical.width) / 2 - logical.x
    ty = size - logical.height - max(1, size * 0.02) - logical.y

    cr.save()
    cr.move_to(tx, ty)
    PangoCairo.layout_path(cr, layout)
    cr.set_source_rgba(0, 0, 0, 0.8)
    cr.set_line_width(max(2.0, size * 0.05))
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.stroke_preserve()
    cr.set_source_rgba(1, 1, 1, 1)
    cr.fill()
    cr.restore()


def is_applet(desktop_id: str) -> bool:
    """True if desktop_id refers to a applet rather than a .desktop app."""
    return is_applet_desktop_id(desktop_id=desktop_id)


def applet_id_from(desktop_id: str) -> AppletId:
    """Extract applet id from desktop_id.

    Handles both simple ids ('applet://clock' -> AppletId.CLOCK) and
    multi-instance ids ('applet://separator#2' -> AppletId.SEPARATOR).
    """
    return _applet_id_from(desktop_id=desktop_id)


class Applet(ABC):
    """Base class for dock plugins that render custom icons.

    Each applet owns a DockItem. The applet renders custom Cairo
    content to a pixbuf and assigns it to item.icon. The existing
    renderer draws it like any other icon -- no renderer changes needed.

    Lifecycle:
      __init__  -> create item + initial icon
      start()   -> begin timers/monitors (called after dock is ready)
      stop()    -> cleanup (called on removal or shutdown)
    """

    id: AppletId
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
            icon=self.create_icon(size=icon_size),
        )
        self._icon_size = icon_size
        self._notify: Callable[[], None] | None = None

    @property
    def desktop_id(self) -> str:
        return applet_desktop_id(applet_id=self.id)

    def load_prefs(self) -> dict[str, Any]:
        """Load this applet's preferences from config."""
        if self._config:
            return dict(self._config.applet_prefs.get(self.id, {}))
        return {}

    def save_prefs(self, prefs: dict[str, Any]) -> None:
        """Save this applet's preferences to config."""
        if self._config:
            self._config.applet_prefs[self.id] = prefs
            self._config.save()

    @abstractmethod
    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render custom content to a pixbuf at the given size."""

    def on_clicked(self) -> None:
        """Handle left-click (default: no-op)."""

    def on_scroll(self, direction_up: bool) -> None:
        """Handle scroll wheel on applet icon (default: no-op)."""

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
        self.item.icon = self.create_icon(size=self._icon_size)
        if self._notify:
            self._notify()
