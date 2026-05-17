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

"""GTK lifecycle glue for the Stretch Coach applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import menu_sections, radio_menu_items
from docking.applets.stretchcoach import meta
from docking.applets.stretchcoach.render import render_icon
from docking.applets.stretchcoach.state import (
    INTERVAL_PRESETS,
    acknowledge_reminder,
    load_cards,
    prefs_from_state,
    set_cards_enabled,
    set_interval,
    show_preview_card,
    state_from_prefs,
    tick,
    tooltip_text,
    trigger_reminder,
)
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config


class StretchCoachApplet(Applet):
    """Periodic micro-break reminder applet with offline stretch cards."""

    id = meta.id
    name = _("Stretch Coach")
    icon_name = "alarm"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        prefs = config.applet_prefs.get("stretchcoach", {}) if config else None
        self._state = state_from_prefs(prefs=prefs)
        self._cards = load_cards()
        self._timer_id: int = 0
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, state=self._state)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        if self._state.due:
            self._state = acknowledge_reminder(self._state)
            self.item.is_urgent = False
        else:
            self._state = trigger_reminder(self._state, cards=self._cards)
            self.item.is_urgent = True
            self.item.last_urgent = GLib.get_monotonic_time()
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        now_label = _("Acknowledge Break") if self._state.due else _("Take Break Now")
        take_break = Gtk.MenuItem(label=now_label)
        take_break.connect("activate", lambda _w: self.on_clicked())

        preview = Gtk.MenuItem(label=_("Show Random Stretch"))
        preview.connect("activate", lambda _w: self._show_random_stretch())

        cards = Gtk.CheckMenuItem(label=_("Random Stretch Cards"))
        cards.set_active(self._state.cards_enabled)
        cards.connect("toggled", self._on_toggle_cards)

        intervals = radio_menu_items(
            choices=tuple(
                (_("{mins} min").format(mins=mins), mins) for mins in INTERVAL_PRESETS
            ),
            active_value=self._state.interval_min,
            on_selected=lambda _widget, value: self._set_interval(value),
            gtk=Gtk,
        )

        return menu_sections(
            primary=[take_break, preview],
            display=[cards, Gtk.SeparatorMenuItem(), *intervals],
            gtk=Gtk,
        )

    def _tick(self) -> bool:
        result = tick(self._state, cards=self._cards)
        self._state = result.state
        if result.became_due:
            self.item.is_urgent = True
            self.item.last_urgent = GLib.get_monotonic_time()
            self.present()
        elif result.should_refresh:
            self.present()
        else:
            self._refresh_tooltip_only()
        return True

    def _show_random_stretch(self) -> None:
        self._state = show_preview_card(self._state, cards=self._cards)
        self.present()

    def _on_toggle_cards(self, widget: Gtk.CheckMenuItem) -> None:
        self._state = set_cards_enabled(self._state, widget.get_active())
        self._save()
        self.present()

    def _set_interval(self, minutes: int) -> None:
        self._state = set_interval(self._state, minutes=minutes)
        self._save()
        self.present()

    def _refresh_tooltip_only(self) -> None:
        self.refresh_tooltip()
        if self._notify:
            self._notify()

    def _save(self) -> None:
        self.save_prefs(prefs_from_state(self._state))
