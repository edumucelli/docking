"""GTK lifecycle glue for hydration applet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.hydration import meta
from docking.applets.hydration.render import render_icon
from docking.applets.hydration.state import (
    INTERVAL_PRESETS,
    prefs_from_state,
    refill,
    set_interval,
    set_show_timer,
    state_from_prefs,
    tick,
    tooltip_text,
    with_fill,
)
from docking.applets.menu import menu_sections, radio_menu_items
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config


class HydrationApplet(Applet):
    """Reminds you to drink water at configurable intervals."""

    id = meta.id
    name = _("Hydration")
    icon_name = "weather-showers"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        prefs = config.applet_prefs.get("hydration", {}) if config else None
        self._state = state_from_prefs(prefs=prefs)
        self._timer_id: int = 0

        super().__init__(icon_size, config)
        self.present()

    # Compatibility accessors used by existing tests
    @property
    def _fill(self) -> float:
        return self._state.fill

    @_fill.setter
    def _fill(self, value: float) -> None:
        self._state = with_fill(state=self._state, fill=value)

    @property
    def _interval_min(self) -> int:
        return self._state.interval_min

    @_interval_min.setter
    def _interval_min(self, value: int) -> None:
        self._state = set_interval(state=self._state, minutes=value)

    @property
    def _show_timer(self) -> bool:
        return self._state.show_timer

    @_show_timer.setter
    def _show_timer(self, value: bool) -> None:
        self._state = set_show_timer(state=self._state, show_timer=value)

    @property
    def _tick_count(self) -> int:
        return self._state.tick_count

    @_tick_count.setter
    def _tick_count(self, value: int) -> None:
        self._state = replace(self._state, tick_count=value)

    def _update_tooltip(self) -> None:
        self.item.name = tooltip_text(
            fill=self._state.fill,
            interval_min=self._state.interval_min,
        )

    def refresh_tooltip(self) -> None:
        self._update_tooltip()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, state=self._state)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        """Refill -- user drank water."""
        self._state = refill(state=self._state)
        self.item.is_urgent = False
        self._update_tooltip()
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        show = Gtk.CheckMenuItem(label=_("Show Timer"))
        show.set_active(self._state.show_timer)
        show.connect("toggled", self._on_toggle_timer)

        intervals = radio_menu_items(
            choices=tuple(
                (_("{mins} min").format(mins=mins), mins) for mins in INTERVAL_PRESETS
            ),
            active_value=self._state.interval_min,
            on_selected=lambda _widget, value: self._set_interval(minutes=value),
            gtk=Gtk,
        )
        return menu_sections(
            display=[show, Gtk.SeparatorMenuItem(), *intervals],
            gtk=Gtk,
        )

    def _on_toggle_timer(self, widget: Gtk.CheckMenuItem) -> None:
        self._state = set_show_timer(
            state=self._state,
            show_timer=widget.get_active(),
        )
        self._save()
        self.present()

    def _tick(self) -> bool:
        result = tick(state=self._state)
        self._state = result.state

        if result.became_empty:
            self.item.is_urgent = True
            self.item.last_urgent = GLib.get_monotonic_time()
            self._update_tooltip()
            self.present()
        elif result.should_refresh:
            self._update_tooltip()
            self.present()

        return True

    def _set_interval(self, minutes: int) -> None:
        self._state = set_interval(state=self._state, minutes=minutes)
        self._save()

    def _save(self) -> None:
        self.save_prefs(prefs=prefs_from_state(state=self._state))
