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

"""GTK lifecycle glue for Pomodoro applet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import menu_sections, radio_menu_items
from docking.applets.pomodoro import meta
from docking.applets.pomodoro.render import render_icon
from docking.applets.pomodoro.state import (
    BREAK_PRESETS,
    LONG_BREAK_PRESETS,
    WORK_PRESETS,
    State,
    click_toggle,
    prefs_from_state,
    reset,
    set_break_minutes,
    set_long_break_minutes,
    set_show_timer,
    set_work_minutes,
    state_from_prefs,
    tick,
    tooltip_text,
)
from docking.i18n import _
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

log = get_logger(name="pomodoro")


class PomodoroApplet(Applet):
    """Pomodoro timer with flat tomato icon."""

    id = meta.id
    name = _("Pomodoro")
    icon_name = "alarm"

    def __init__(self, icon_size: int, config: Config) -> None:
        prefs = config.applet_prefs.get("pomodoro", {})
        self._data = state_from_prefs(prefs=prefs)
        self._timer_id: int = 0

        super().__init__(icon_size, config)
        self.present()

    # Compatibility accessors used by existing tests
    @property
    def _state(self) -> State:
        return self._data.phase

    @_state.setter
    def _state(self, value: State) -> None:
        self._data = replace(self._data, phase=value)

    @property
    def _paused_from(self) -> State:
        return self._data.paused_from

    @_paused_from.setter
    def _paused_from(self, value: State) -> None:
        self._data = replace(self._data, paused_from=value)

    @property
    def _remaining(self) -> int:
        return self._data.remaining

    @_remaining.setter
    def _remaining(self, value: int) -> None:
        self._data = replace(self._data, remaining=value)

    @property
    def _work_count(self) -> int:
        return self._data.work_count

    @_work_count.setter
    def _work_count(self, value: int) -> None:
        self._data = replace(self._data, work_count=value)

    @property
    def _work_min(self) -> int:
        return self._data.work_min

    @_work_min.setter
    def _work_min(self, value: int) -> None:
        self._data = set_work_minutes(state=self._data, minutes=value)

    @property
    def _break_min(self) -> int:
        return self._data.break_min

    @_break_min.setter
    def _break_min(self, value: int) -> None:
        self._data = set_break_minutes(state=self._data, minutes=value)

    @property
    def _long_break_min(self) -> int:
        return self._data.long_break_min

    @_long_break_min.setter
    def _long_break_min(self, value: int) -> None:
        self._data = set_long_break_minutes(state=self._data, minutes=value)

    @property
    def _show_timer(self) -> bool:
        return self._data.show_timer

    @_show_timer.setter
    def _show_timer(self, value: bool) -> None:
        self._data = set_show_timer(state=self._data, show_timer=value)

    def _update_tooltip(self) -> None:
        self.item.name = tooltip_text(
            state=self._data.phase, remaining=self._data.remaining
        )

    def refresh_tooltip(self) -> None:
        self._update_tooltip()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, state=self._data)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    # -- Interaction ---------------------------------------------------------

    def on_clicked(self) -> None:
        """Start/pause toggle."""
        self._data = click_toggle(state=self._data)
        self._update_tooltip()
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        reset_item = Gtk.MenuItem(label=_("Reset"))
        reset_item.connect("activate", lambda _w: self._reset())

        show = Gtk.CheckMenuItem(label=_("Show Timer"))
        show.set_active(self._data.show_timer)
        show.connect("toggled", self._on_toggle_timer)

        display: list[Gtk.MenuItem] = [show, Gtk.SeparatorMenuItem()]
        display.append(self._make_duration_header(label=_("Work")))
        display.extend(
            radio_menu_items(
                choices=tuple(
                    (_("{mins} min").format(mins=mins), mins) for mins in WORK_PRESETS
                ),
                active_value=self._data.work_min,
                on_selected=lambda _widget, value: self._set_work(minutes=value),
                gtk=Gtk,
            )
        )

        display.append(Gtk.SeparatorMenuItem())
        display.append(self._make_duration_header(label=_("Break")))
        display.extend(
            radio_menu_items(
                choices=tuple(
                    (_("{mins} min").format(mins=mins), mins) for mins in BREAK_PRESETS
                ),
                active_value=self._data.break_min,
                on_selected=lambda _widget, value: self._set_break(minutes=value),
                gtk=Gtk,
            )
        )

        display.append(Gtk.SeparatorMenuItem())
        display.append(self._make_duration_header(label=_("Long Break")))
        display.extend(
            radio_menu_items(
                choices=tuple(
                    (_("{mins} min").format(mins=mins), mins)
                    for mins in LONG_BREAK_PRESETS
                ),
                active_value=self._data.long_break_min,
                on_selected=lambda _widget, value: self._set_long_break(minutes=value),
                gtk=Gtk,
            )
        )

        return menu_sections(display=display, destructive=[reset_item], gtk=Gtk)

    # -- Internals -----------------------------------------------------------

    def _tick(self) -> bool:
        if self._data.phase in (State.IDLE, State.PAUSED):
            return True
        result = tick(state=self._data)
        self._data = result.state
        if result.phase_changed:
            # Trigger urgent bounce+glow to notify phase change
            self.item.is_urgent = True
            self.item.last_urgent = GLib.get_monotonic_time()
        self._update_tooltip()
        self.present()
        return True

    def _reset(self) -> None:
        self._data = reset(state=self._data)
        self._update_tooltip()
        self.present()

    def _save(self) -> None:
        self.save_prefs(prefs=prefs_from_state(state=self._data))

    def _on_toggle_timer(self, widget: Gtk.CheckMenuItem) -> None:
        self._data = set_show_timer(state=self._data, show_timer=widget.get_active())
        self._save()
        self.present()

    def _set_work(self, minutes: int) -> None:
        self._data = set_work_minutes(state=self._data, minutes=minutes)
        self._save()

    def _set_break(self, minutes: int) -> None:
        self._data = set_break_minutes(state=self._data, minutes=minutes)
        self._save()

    def _set_long_break(self, minutes: int) -> None:
        self._data = set_long_break_minutes(state=self._data, minutes=minutes)
        self._save()

    @staticmethod
    def _make_duration_header(label: str) -> Gtk.MenuItem:
        menu_item = Gtk.MenuItem(label=label)
        menu_item.set_sensitive(False)
        return menu_item
