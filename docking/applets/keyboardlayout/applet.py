"""GTK lifecycle glue for keyboard layout applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.keyboardlayout import meta
from docking.applets.keyboardlayout.render import render_icon
from docking.applets.keyboardlayout.state import (
    LayoutState,
    cycle_layout,
    detect_backend,
    layout_label,
    tooltip_text,
)
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(
    get_logger(name="keyboardlayout"),
    applet_id=meta.id,
)

POLL_INTERVAL_S = 2


class KeyboardLayoutApplet(Applet):
    """Switch and display the current keyboard layout."""

    id = meta.id
    name = _("Keyboard Layout")
    icon_name = "input-keyboard"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._backend = detect_backend()
        self._layout = LayoutState(active="", available=[])
        self._timer_id: int = 0
        self._last_label: str = ""
        self._poll()
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _active(self) -> str:
        return self._layout.active

    @property
    def _available(self) -> list[str]:
        return self._layout.available

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        label = layout_label(code=self._active) if self._active else "??"
        self._last_label = label
        return render_icon(size=size, label=label)

    def refresh_tooltip(self) -> None:
        if self._active:
            self.item.name = tooltip_text(active=self._active)
        else:
            self.item.name = _("No keyboard layout detected")

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(
            POLL_INTERVAL_S,
            self._tick,
        )

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        """Cycle to next layout on click."""
        if len(self._available) < 2:
            return
        next_code = cycle_layout(
            current=self._active,
            available=self._available,
        )
        self._backend.switch(layout_code=next_code)
        self._layout = self._layout._replace(active=next_code)
        self.refresh_tooltip()
        self.present()

    def on_scroll(self, direction_up: bool) -> None:
        """Scroll through available layouts."""
        if len(self._available) < 2:
            return
        order = self._available if direction_up else list(reversed(self._available))
        next_code = cycle_layout(
            current=self._active,
            available=order,
        )
        self._backend.switch(layout_code=next_code)
        self._layout = self._layout._replace(active=next_code)
        self.refresh_tooltip()
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []
        for code in self._available:
            prefix = "\u2022 " if code == self._active else "  "
            label = f"{prefix}{layout_label(code=code)} — {code}"
            mi = Gtk.MenuItem(label=label)
            mi.connect(
                "activate",
                lambda _w, c=code: self._select_layout(code=c),
            )
            items.append(mi)
        return items

    def _select_layout(self, code: str) -> None:
        self._backend.switch(layout_code=code)
        self._layout = self._layout._replace(active=code)
        self.refresh_tooltip()
        self.present()

    def _poll(self) -> None:
        self._layout = self._backend.query()

    def _tick(self) -> bool:
        new_layout = self._backend.query()
        changed = (
            new_layout.active != self._layout.active
            or new_layout.available != self._layout.available
        )
        self._layout = new_layout
        if changed:
            self.refresh_tooltip()
            self.present()
        return True
