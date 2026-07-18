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

"""Base applet contract and shared icon-rendering utilities.

Every dock applet (clock, weather, quote, hydration, and so on) is a small UI
module that owns one ``DockItem`` and knows how to render that item icon.
This file defines the common lifecycle and update contract all applets follow.

Core applet contract

Applets split their work into two explicit paths:

1. ``create_icon(size)``: pure visual rendering to pixbuf,
2. ``refresh_tooltip()``: update user-facing metadata (name/tooltip builder),
3. ``present()``: perform both paths and notify the dock model/UI.

Why this split matters

The current applet contract keeps rendering and metadata updates explicit.
``create_icon()`` is for pixels, ``refresh_tooltip()`` is for user-facing text,
``present()`` is the coordinated update path for later state
changes, and subclasses call ``present()`` once their own initialization state
is ready.
This keeps applet behavior predictable and testable:

- icon tests can assert drawing behavior independently,
- tooltip/name tests can assert metadata logic independently,
- full refresh paths are explicit at call sites.

Applet lifecycle model

Applets are instantiated by DockModel with icon size and config, then started
after dock startup. Typical lifecycle:

1. ``__init__`` creates the DockItem,
2. ``present()`` performs the initial presentation sync once subclass init is complete,
3. ``start(notify=...)`` enables timers/watchers/signal subscriptions,
4. applet internals call ``present()`` on state changes,
5. ``stop()`` tears down timers/watchers when removed or on shutdown.

Shared helpers provided here

This module also centralizes reusable rendering helpers and icon-theme loading:

- theme icon lookup with fallback candidate names,
- bundled fallback icon for applet identity consistency across distros,
- shared outlined text drawing used by multiple applets.

Keeping these helpers in one place avoids per-applet duplication and ensures
consistent visual behavior across applets.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from importlib import resources
from typing import TYPE_CHECKING, Any

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk, Pango, PangoCairo

from docking.applets.identity import applet_desktop_id
from docking.applets.popup import PopupAnchor
from docking.core.icons import ICON_SOURCE_PREF_KEY, IconSource, icon_source_from_value
from docking.core.items import APPLET_KIND, DockItem
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.applets.services import AppletServices
    from docking.core.config import Config
    from docking.ui.stack import StackContent

log = get_logger("applets.base")

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

CATALOG_ICON_DIR = "icons/applets"

ICON_SOURCE_DOCKING = IconSource.DOCKING.value
ICON_SOURCE_SYSTEM = IconSource.SYSTEM.value
ICON_SOURCE_VALUES = frozenset(source.value for source in IconSource)


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
    except (FileNotFoundError, GLib.Error, ModuleNotFoundError) as exc:
        log.debug("Failed to load bundled applet fallback icon: %s", exc)
        return None


def load_theme_icon(name: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load an icon by name from GTK icon themes with Docking fallbacks.

    Lookup expands known aliases and symbolic variants, then tries the default
    theme plus Adwaita/hicolor fallbacks for CI and sparse desktop sessions.
    For built-in applet names that Docking guarantees visually, missing theme
    icons fall back to the bundled applet placeholder asset.
    """
    flags = Gtk.IconLookupFlags.FORCE_SIZE
    for icon_name in _icon_name_candidates(name=name):
        for theme in _icon_theme_candidates():
            icon_info = theme.lookup_icon(icon_name, size, flags)
            if icon_info is None:
                continue
            try:
                return icon_info.load_icon()
            except GLib.Error as exc:
                log.debug(
                    "Theme icon lookup failed for %s at size %s: %s",
                    icon_name,
                    size,
                    exc,
                )
                continue
    if _should_use_bundled_fallback(name=name):
        return _load_bundled_fallback_icon(size=size)
    return None


def load_catalog_icon(*, applet_id: str, size: int) -> GdkPixbuf.Pixbuf | None:
    """Load one generated catalog icon for menu/settings applet catalogs.

    These assets are pre-generated by ``tools/generate_applet_catalog_icons.py``
    and are intentionally strict: missing assets return ``None`` and do not
    trigger a live/theme fallback.
    """
    icon_ref = (
        resources.files("docking.assets")
        .joinpath(CATALOG_ICON_DIR)
        .joinpath(f"{applet_id}.png")
    )
    try:
        with resources.as_file(icon_ref) as icon_path:
            return GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(icon_path), size, size, True
            )
    except (FileNotFoundError, GLib.Error, ModuleNotFoundError) as exc:
        log.debug("Failed to load catalog icon for %s: %s", applet_id, exc)
        return None


RGBA = tuple[float, float, float, float]

_ICON_LABEL_FONT_SCALE = 0.22
_ICON_LABEL_MIN_FONT_SCALE = 0.12
_ICON_LABEL_MAX_WIDTH_SCALE = 0.92
_ICON_LABEL_OUTLINE_SCALE = 0.22


def draw_icon_label(
    cr: cairo.Context,
    text: str,
    size: int,
    *,
    max_width: float | None = None,
    fill_rgba: RGBA = (1, 1, 1, 1),
    outline_rgba: RGBA = (0, 0, 0, 0.8),
) -> None:
    """Draw outlined text at the bottom center of a size x size icon.

    Shared by weather (temperature), pomodoro (countdown), and hydration
    (countdown) for a uniform appearance.
    """
    if not text:
        return

    target_width = max(1.0, float(max_width or size * _ICON_LABEL_MAX_WIDTH_SCALE))
    initial_font_size = max(1, int(size * _ICON_LABEL_FONT_SCALE))
    min_font_size = max(
        1, min(initial_font_size, int(size * _ICON_LABEL_MIN_FONT_SCALE))
    )
    layout, logical, final_font_size = _fit_icon_label_layout(
        cr=cr,
        text=text,
        max_width=target_width,
        initial_font_size=initial_font_size,
        min_font_size=min_font_size,
    )

    tx, ty = _icon_label_origin(
        size=size,
        logical=logical,
        bottom_padding=max(1, size * 0.02),
    )

    cr.save()
    cr.move_to(tx, ty)
    PangoCairo.layout_path(cr, layout)
    cr.set_source_rgba(*outline_rgba)
    cr.set_line_width(_icon_label_outline_width(font_size=final_font_size))
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    cr.stroke_preserve()
    cr.set_source_rgba(*fill_rgba)
    cr.fill()
    cr.restore()


def _fit_icon_label_layout(
    *,
    cr: cairo.Context,
    text: str,
    max_width: float,
    initial_font_size: int,
    min_font_size: int,
) -> tuple[Pango.Layout, Pango.Rectangle, int]:
    """Build a Pango layout, shrinking the font until it fits max_width."""
    for font_size in range(initial_font_size, min_font_size - 1, -1):
        layout = _icon_label_layout(cr=cr, text=text, font_size=font_size)
        _, logical = layout.get_pixel_extents()
        if logical.width <= max_width or font_size == min_font_size:
            return layout, logical, font_size
    layout = _icon_label_layout(cr=cr, text=text, font_size=min_font_size)
    _, logical = layout.get_pixel_extents()
    return layout, logical, min_font_size


def _icon_label_layout(
    *,
    cr: cairo.Context,
    text: str,
    font_size: int,
) -> Pango.Layout:
    layout = PangoCairo.create_layout(cr)
    layout.set_font_description(
        Pango.FontDescription.from_string(f"Sans Bold {font_size}px")
    )
    layout.set_text(text, -1)
    return layout


def _icon_label_origin(
    *,
    size: int,
    logical: Pango.Rectangle,
    bottom_padding: float,
) -> tuple[float, float]:
    tx = (size - logical.width) / 2 - logical.x
    ty = size - logical.height - bottom_padding - logical.y
    return tx, ty


def _icon_label_outline_width(*, font_size: int) -> float:
    return max(1.0, font_size * _ICON_LABEL_OUTLINE_SCALE)


class Applet(ABC):
    """Base class for dock plugins that render Docking icons.

    Each applet owns a DockItem. Most applets render their own pixbuf, while
    simple applets can opt into a user-selected system theme icon. The existing
    renderer draws both paths like any other item icon.

    Lifecycle:
      __init__  -> create item
      present() -> initial presentation sync once subclass state is ready
      present() -> later icon/tooltip redraws after applet state changes
      start()   -> begin timers/monitors (called after dock is ready)
      stop()    -> cleanup (called on removal or shutdown)
    """

    id: str
    name: str
    icon_name: str
    icon_source_options: tuple[IconSource, ...] = (IconSource.DOCKING,)

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._config = config
        self._icon_size = icon_size
        self._notify: Callable[[], None] | None = None
        self._popup_anchor: PopupAnchor | None = None
        self.item = DockItem(
            desktop_id=self.desktop_id,
            kind=APPLET_KIND,
            target=self.desktop_id,
            name=self.name,
            icon_name=self.icon_name,
            is_pinned=True,
            icon=None,
            prefs_key=self.desktop_id,
        )

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

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render the active icon source at the given size."""
        if self.uses_system_icon():
            icon_name = self.system_icon_name()
            icon = load_theme_icon(name=icon_name, size=size)
            if icon is not None:
                self.item.icon_name = icon_name
                return icon

        self.item.icon_name = self.icon_name
        return self.create_docking_icon(size=size)

    def create_docking_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render the built-in Docking icon when an applet opts into icon sources."""
        _ = size
        raise NotImplementedError(
            f"{type(self).__name__} must implement create_icon() "
            "or create_docking_icon()"
        )

    def system_icon_name(self) -> str:
        """Theme icon name used when the applet is set to System Icon."""
        return self.icon_name

    def _declared_icon_source_options(self) -> tuple[IconSource, ...]:
        declared = getattr(
            type(self),
            "icon_source_options",
            Applet.icon_source_options,
        )
        if declared is not Applet.icon_source_options:
            options: list[IconSource] = []
            for source in declared:
                parsed = icon_source_from_value(source)
                if parsed is not None and parsed not in options:
                    options.append(parsed)
            return tuple(options) or (IconSource.DOCKING,)
        return Applet.icon_source_options

    def supports_icon_source(self, source: IconSource | str) -> bool:
        """Whether this applet exposes the requested icon source."""
        parsed = icon_source_from_value(source)
        return parsed is not None and parsed in self._declared_icon_source_options()

    def icon_source(self) -> str:
        """Return the selected icon source, defaulting to the Docking icon."""
        source = icon_source_from_value(self.load_prefs().get(ICON_SOURCE_PREF_KEY))
        if source is not None and self.supports_icon_source(source):
            return source.value
        return IconSource.DOCKING.value

    def uses_system_icon(self) -> bool:
        """Whether this applet currently requests a theme icon."""
        return self.icon_source() == IconSource.SYSTEM.value

    def set_icon_source(self, source: IconSource | str) -> None:
        """Persist and present the selected icon source."""
        parsed = icon_source_from_value(source)
        if parsed is None or not self.supports_icon_source(parsed):
            return
        if parsed.value == self.icon_source():
            return

        prefs = self.load_prefs()
        prefs[ICON_SOURCE_PREF_KEY] = parsed.value
        self.save_prefs(prefs)
        self.present()

    def refresh_tooltip(self) -> None:
        """Sync tooltip/text presentation fields on self.item."""
        return

    def on_clicked(self) -> None:
        """Handle left-click (default: no-op)."""
        return

    def stack_content(self, icon_size: int) -> StackContent | None:
        """Return reusable stack content, or None for normal click handling."""
        _ = icon_size
        return None

    @property
    def popup_anchor(self) -> PopupAnchor | None:
        """Most recent dock icon anchor for applet-owned popup surfaces."""
        return self._popup_anchor

    def set_popup_anchor(self, anchor: PopupAnchor | None) -> None:
        """Update the dock icon anchor used by applet-owned popup surfaces."""
        self._popup_anchor = anchor

    def on_scroll(self, direction_up: bool) -> None:
        """Handle scroll wheel on applet icon (default: no-op)."""
        _ = direction_up
        return

    def accepts_drop_uris(self) -> bool:
        """Whether this applet can receive external URI drops."""
        return False

    def on_drop_uris(self, uris: list[str]) -> bool:
        """Handle external URI drops on the applet icon.

        Returns True when the applet consumed the drop.
        """
        _ = uris
        return False

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Extra right-click menu items (default: empty)."""
        return []

    def apply_prefs(self) -> None:
        """Apply persisted preferences after desktop_id is finalized.

        Applets that need per-instance or late-bound preference loading can
        override this method. Default applets have nothing to apply.
        """
        return

    def set_services(self, services: AppletServices) -> None:
        """Attach backend services; applets that need them override this hook."""
        _ = services
        return

    def start(self, notify: Callable[[], None]) -> None:
        """Start timers/monitors. Call notify() to trigger redraw."""
        self._notify = notify

    def stop(self) -> None:
        """Cleanup timers/monitors."""
        self._notify = None

    def present(self) -> None:
        """Refresh icon + tooltip fields and trigger a redraw."""
        self.item.icon = self.create_icon(size=self._icon_size)
        self.refresh_tooltip()
        if self._notify:
            self._notify()
