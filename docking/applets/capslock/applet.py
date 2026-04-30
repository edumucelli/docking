"""GTK lifecycle glue for Caps Lock applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.capslock import meta
from docking.applets.capslock.render import render_icon
from docking.applets.capslock.state import (
    POLL_INTERVAL_S,
    LockKeyState,
    menu_label,
    query_lock_state,
    tooltip_text,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="capslock"), applet_id=meta.id)


class CapslockApplet(Applet):
    """Show Caps Lock and Num Lock state for keyboards without LEDs."""

    id = meta.id
    name = _("Caps Lock")
    icon_name = "input-keyboard"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._state: LockKeyState = query_lock_state()
        self._timer_id: int = 0
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, state=self._state)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        self._refresh_now()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []
        if not self._state.available:
            status.append(
                disabled_menu_item(_("Keyboard lock state unavailable"), gtk=Gtk)
            )
        else:
            status.append(
                disabled_menu_item(
                    menu_label(_("Caps Lock"), self._state.caps_lock), gtk=Gtk
                )
            )
            status.append(
                disabled_menu_item(
                    menu_label(_("Num Lock"), self._state.num_lock), gtk=Gtk
                )
            )

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._refresh_now())
        return menu_sections(status=status, refresh=[refresh], gtk=Gtk)

    def _refresh_now(self) -> None:
        self._state = query_lock_state()
        self.present()

    def _tick(self) -> bool:
        old_state = self._state
        self._state = query_lock_state()
        if self._state != old_state:
            self.present()
        return True
