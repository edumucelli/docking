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

"""GTK lifecycle glue for the Sunrise applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.cities import search_cities
from docking.applets.menu import disabled_menu_item, menu_sections, radio_submenu
from docking.applets.popup import prepare_dialog_content
from docking.applets.sunrise import meta
from docking.applets.sunrise.render import render_icon
from docking.applets.sunrise.state import (
    REFRESH_INTERVAL_S,
    CityPref,
    LabelMode,
    SolarSnapshot,
    build_snapshot,
    cached_cities,
    cycle_active_index,
    label_mode_label,
    menu_header_label,
    prefs_from_mapping,
    prefs_payload,
    tooltip_text,
)
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

CITY_DIALOG_WIDTH_PX = 350
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12
CITY_SEARCH_MIN_CHARS = 2
CITY_SEARCH_RESULT_LIMIT = 10


class SunriseApplet(Applet):
    """Shows sunrise/sunset countdown and a solar phase dial."""

    id = meta.id
    name = _("Sunrise")
    icon_name = "weather-clear"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        prefs = prefs_from_mapping(
            config.applet_prefs.get("sunrise", {}) if config else None
        )
        self._cities: list[CityPref] = list(prefs.cities)
        self._active_index: int = prefs.active_index
        self._label_mode: LabelMode = prefs.label_mode
        self._snapshot: SolarSnapshot = build_snapshot(
            city=self._active_city,
            label_mode=self._label_mode,
        )
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _active_city(self) -> CityPref | None:
        if self._cities:
            return self._cities[self._active_index]
        return None

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, snapshot=self._snapshot)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(self._snapshot)

    def on_clicked(self) -> None:
        self._show_city_dialog()

    def on_scroll(self, direction_up: bool) -> None:
        if len(self._cities) <= 1:
            return
        self._active_index = cycle_active_index(
            count=len(self._cities),
            current=self._active_index,
            direction_up=direction_up,
        )
        self._save_prefs()
        self._refresh_snapshot()
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status = [
            disabled_menu_item(menu_header_label(self._snapshot), gtk=Gtk),
        ]
        if self._active_city is not None:
            status.append(
                disabled_menu_item(
                    _("Times shown in system timezone"),
                    gtk=Gtk,
                )
            )

        change_city = Gtk.MenuItem(label=_("Change City..."))
        change_city.connect("activate", lambda _w: self._show_city_dialog())

        display = [
            radio_submenu(
                label=_("Label Mode"),
                choices=tuple(
                    (label_mode_label(mode), mode)
                    for mode in (
                        LabelMode.NEXT_EVENT,
                        LabelMode.PHASE,
                        LabelMode.SUNRISE_SUNSET,
                    )
                ),
                active_value=self._label_mode,
                on_selected=lambda widget, value: self._on_label_mode_selected(
                    widget=widget,
                    label_mode=value,
                ),
                gtk=Gtk,
            )
        ]

        destructive: list[Gtk.MenuItem] = []
        active = self._active_city
        if active and len(self._cities) > 1:
            remove = Gtk.MenuItem(
                label=_("Remove {city}").format(city=active.city_display)
            )
            remove.connect("activate", lambda _w: self._remove_active_city())
            destructive.append(remove)

        return menu_sections(
            status=status,
            refresh=[change_city],
            display=display,
            destructive=destructive,
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL_S, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def _tick(self) -> bool:
        self._refresh_snapshot()
        self.present()
        return True

    def _refresh_snapshot(self) -> None:
        self._snapshot = build_snapshot(
            city=self._active_city,
            now=dt.datetime.now().astimezone(),
            label_mode=self._label_mode,
        )

    def _show_city_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Search for the city"),
            modal=True,
            destroy_with_parent=True,
        )
        self.register_popup_surface(dialog)
        dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dialog.connect("response", lambda dlg, _response: dlg.destroy())
        box = prepare_dialog_content(
            dialog=dialog,
            width=CITY_DIALOG_WIDTH_PX,
            spacing=DIALOG_CONTENT_SPACING_PX,
            margin=DIALOG_HORIZONTAL_MARGIN_PX,
        )

        entry = Gtk.Entry()
        entry.set_placeholder_text(_("Type city name..."))
        box.pack_start(entry, False, False, 0)

        completion = Gtk.EntryCompletion()
        store = Gtk.ListStore(str, float, float)
        completion.set_model(store)
        completion.set_text_column(0)
        completion.set_minimum_key_length(CITY_SEARCH_MIN_CHARS)

        def on_changed(changed_entry: Gtk.Entry) -> None:
            text = changed_entry.get_text()
            store.clear()
            if len(text) < CITY_SEARCH_MIN_CHARS:
                return
            for city in search_cities(
                text,
                cached_cities(),
                limit=CITY_SEARCH_RESULT_LIMIT,
            ):
                store.append([city.display, city.lat, city.lng])

        def on_match_selected(
            _completion: Gtk.EntryCompletion,
            model: Gtk.TreeModel,
            tree_iter: Gtk.TreeIter,
        ) -> bool:
            display = model.get_value(tree_iter, 0)
            lat = model.get_value(tree_iter, 1)
            lng = model.get_value(tree_iter, 2)
            self._add_city(display=display, lat=lat, lng=lng)

            def destroy_dialog() -> bool:
                dialog.destroy()
                return False

            GLib.idle_add(destroy_dialog)
            return True

        entry.connect("changed", on_changed)
        completion.connect("match-selected", on_match_selected)
        entry.set_completion(completion)

        dialog.show_all()
        entry.grab_focus()

    def _add_city(self, display: str, lat: float, lng: float) -> None:
        for i, city in enumerate(self._cities):
            if city.lat == lat and city.lng == lng:
                self._active_index = i
                self._save_prefs()
                self._refresh_snapshot()
                self.present()
                return
        self._cities.append(CityPref(city_display=display, lat=lat, lng=lng))
        self._active_index = len(self._cities) - 1
        self._save_prefs()
        self._refresh_snapshot()
        self.present()

    def _remove_active_city(self) -> None:
        if len(self._cities) <= 1:
            return
        del self._cities[self._active_index]
        self._active_index = min(self._active_index, len(self._cities) - 1)
        self._save_prefs()
        self._refresh_snapshot()
        self.present()

    def _on_label_mode_selected(
        self,
        *,
        widget: Gtk.RadioMenuItem,
        label_mode: LabelMode,
    ) -> None:
        if not widget.get_active():
            return
        if label_mode == self._label_mode:
            return
        self._label_mode = label_mode
        self._save_prefs()
        self._refresh_snapshot()
        self.present()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                cities=tuple(self._cities),
                active_index=self._active_index,
                label_mode=self._label_mode,
            )
        )
