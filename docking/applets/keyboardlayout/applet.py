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

"""Shared keyboard layout presentation and serialized input requests."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.keyboardlayout import meta
from docking.applets.keyboardlayout.backend import create_backend
from docking.applets.keyboardlayout.render import render_icon
from docking.applets.keyboardlayout.state import (
    LayoutState,
    cycle_layout,
    layout_display_name,
    layout_label,
    tooltip_text,
)
from docking.applets.menu import menu_sections, radio_menu_items
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="keyboardlayout"), applet_id=meta.id)
POLL_INTERVAL_S = 2


class KeyboardLayoutApplet(Applet):
    """Switch and display the current input source through its platform backend."""

    id = meta.id
    name = _("Keyboard Layout")
    icon_name = "input-keyboard"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._backend = create_backend()
        self._layout = LayoutState(active="", available=[])
        self._timer_id = 0
        self._last_label = ""
        self._worker = BackgroundWorker(logger=log)
        self._generation = 0
        self._stopped = False
        self._set_in_progress = False
        self._pending_layout: tuple[str, str] | None = None
        super().__init__(icon_size=icon_size, config=config)
        self.present()
        self._tick()

    @property
    def _active(self) -> str:
        return self._layout.active

    @property
    def _available(self) -> list[str]:
        return self._layout.available

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        label = (
            layout_label(code=self._active, state=self._layout)
            if self._active
            else "??"
        )
        self._last_label = label
        return render_icon(size=size, label=label)

    def refresh_tooltip(self) -> None:
        self.item.name = (
            tooltip_text(active=self._active, state=self._layout)
            if self._active
            else _("No keyboard layout detected")
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        self._stopped = True
        self._generation += 1
        self._pending_layout = None
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._backend.stop()
        super().stop()

    def _prepare(self) -> None:
        new = self._backend.prepare_interaction(self._layout)
        if (
            self._set_in_progress
            and new.context == self._layout.context
            and self._active in new.available
        ):
            new = new._replace(active=self._active)
        if new != self._layout:
            self._generation += 1
            self._layout = new
            self.present()

    def on_clicked(self) -> None:
        self.on_scroll(direction_up=True)

    def on_scroll(self, direction_up: bool) -> None:
        if self._stopped:
            return
        self._prepare()
        if len(self._available) < 2:
            return
        order = self._available if direction_up else list(reversed(self._available))
        self._select_layout(
            cycle_layout(current=self._active, available=order),
            context=self._layout.context,
        )

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        self._prepare()
        context = self._layout.context
        settings: list[Gtk.MenuItem] = []
        if self._backend.settings_available():
            item = Gtk.MenuItem(label=_("Keyboard Settings"))
            item.connect("activate", lambda _widget: self._open_settings())
            settings.append(item)
        primary: list[Gtk.MenuItem] = []
        if self._active and self._backend.viewer_available(self._active):
            item = Gtk.MenuItem(label=_("Show Current Layout"))
            item.connect("activate", lambda _widget: self._show_layout())
            primary.append(item)
        detailed_codes = {info.code for info in self._layout.details}
        choices = []
        for code in self._available:
            label = layout_label(code, state=self._layout)
            name = (
                layout_display_name(code, state=self._layout)
                if code in detailed_codes
                else code
            )
            choices.append((f"{label} - {name}", code))
        display = radio_menu_items(
            choices=tuple(choices),
            active_value=self._active,
            on_selected=lambda _widget, code: self._select_layout(
                code, context=context
            ),
            gtk=Gtk,
        )
        return menu_sections(
            primary=primary, display=display, settings=settings, gtk=Gtk
        )

    def _open_settings(self) -> None:
        if not self._stopped:
            self._worker.run(
                name="keyboard-settings",
                fn=lambda: False if self._stopped else self._backend.open_settings(),
            )

    def _show_layout(self) -> None:
        if not self._stopped:
            code = self._active
            self._worker.run(
                name="keyboard-viewer",
                fn=lambda: False if self._stopped else self._backend.show_layout(code),
            )

    def _select_layout(self, code: str, *, context: str | None = None) -> None:
        if self._stopped or code not in self._available:
            return
        self._generation += 1
        self._pending_layout = (
            code,
            self._layout.context if context is None else context,
        )
        self._layout = self._layout._replace(active=code)
        self.present()
        self._start_pending()

    def _start_pending(self) -> None:
        if self._stopped or self._set_in_progress or self._pending_layout is None:
            return
        code, context = self._pending_layout
        self._pending_layout = None
        self._set_in_progress = True

        def apply():
            if self._stopped:
                return LayoutState("", [])
            self._backend.switch(layout_code=code, context=context)
            return self._backend.query()

        self._worker.run(
            name="keyboard-switch",
            fn=apply,
            on_result=self._on_set,
            on_error=lambda _error: self._on_set(LayoutState("", [])),
        )

    def _on_set(self, state: LayoutState) -> bool:
        self._set_in_progress = False
        if self._stopped:
            return False
        if self._pending_layout is None or state.context != self._pending_layout[1]:
            self._pending_layout = None
            self._layout = state
            self.present()
        self._start_pending()
        return False

    def _tick(self) -> bool:
        if self._stopped:
            return False
        if self._set_in_progress:
            return True
        generation = self._generation
        self._worker.run_guarded(
            key="keyboard-poll",
            name="keyboard-poll",
            fn=lambda: LayoutState("", []) if self._stopped else self._backend.query(),
            on_result=lambda state: self._on_poll(state, generation),
            on_error=lambda _error: self._on_poll(LayoutState("", []), generation),
        )
        return True

    def _on_poll(self, state: LayoutState, generation: int) -> bool:
        if self._stopped or self._set_in_progress or generation != self._generation:
            return False
        if state != self._layout:
            self._layout = state
            self.present()
        return False
