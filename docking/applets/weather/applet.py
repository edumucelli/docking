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

"""GTK lifecycle glue for weather applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.cities import search_cities
from docking.applets.freshness import cadence_label, updated_label
from docking.applets.live_state import (
    live_state_error,
    live_state_label,
    resolve_live_status,
)
from docking.applets.menu import disabled_menu_item, menu_sections, radio_submenu
from docking.applets.popup import prepare_dialog_content
from docking.applets.weather import meta
from docking.applets.weather.api import (
    REFRESH_INTERVAL,
    AirQualityData,
    WeatherData,
    fetch_air_quality,
    fetch_weather,
    wmo_icon_name,
)
from docking.applets.weather.render import create_icon
from docking.applets.weather.state import (
    CityPref,
    TemperatureUnit,
    build_tooltip,
    cached_cities,
    cycle_active_index,
    format_temperature,
    format_temperature_range,
    menu_header_label,
    prefs_from_mapping,
    prefs_payload,
    temperature_unit_label,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="weather"), applet_id=meta.id)

CITY_DIALOG_WIDTH_PX = 350
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12
CITY_SEARCH_MIN_CHARS = 2
CITY_SEARCH_RESULT_LIMIT = 10
STARTUP_FETCH_DELAY_S = 1


class WeatherApplet(Applet):
    """Shows current weather icon + temperature for a selected city."""

    id = meta.id
    name = _("Weather")
    icon_name = "weather-few-clouds"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._timer_id: int = 0
        self._startup_fetch_timer_id: int = 0
        self._fetch_request_id: int = 0
        self._weather: WeatherData | None = None
        self._air_quality: AirQualityData | None = None
        self._last_updated: dt.datetime | None = None
        self._loading = False
        self._fetch_pending = False
        self._fetch_failed = False
        self._fetch_error = ""
        self._worker = BackgroundWorker(logger=log)

        prefs = prefs_from_mapping(config.applet_prefs.get("weather", {}))
        self._cities: list[CityPref] = list(prefs.cities)
        self._active_index: int = prefs.active_index
        self._show_temperature = prefs.show_temperature
        self._temperature_unit = prefs.temperature_unit

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    @property
    def _active_city(self) -> CityPref | None:
        if self._cities:
            return self._cities[self._active_index]
        return None

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(
            size=size,
            weather=self._weather,
            show_temperature=self._show_temperature,
            temperature_unit=self._temperature_unit,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = self._build_tooltip()
        self.item.tooltip_builder = self._build_tooltip_widget

    def on_clicked(self) -> None:
        self._show_city_dialog()

    def on_scroll(self, direction_up: bool) -> None:
        """Cycle through cities on scroll."""
        if len(self._cities) <= 1:
            return
        self._active_index = cycle_active_index(
            count=len(self._cities),
            current=self._active_index,
            direction_up=direction_up,
        )
        self._weather = None
        self._air_quality = None
        self._last_updated = None
        self._loading = False
        self._fetch_pending = False
        self._fetch_failed = False
        self._fetch_error = ""
        self._save_prefs()
        self._fetch_async()
        self.present()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []
        active = self._active_city

        if active:
            status.append(
                disabled_menu_item(
                    menu_header_label(
                        city_display=active.city_display,
                        weather=self._weather,
                        temperature_unit=self._temperature_unit,
                    ),
                    gtk=Gtk,
                )
            )
            status.append(
                disabled_menu_item(
                    cadence_label(seconds=REFRESH_INTERVAL),
                    gtk=Gtk,
                )
            )
            state_status = self._live_status()
            state_label = live_state_label(state_status)
            if state_label:
                status.append(disabled_menu_item(state_label, gtk=Gtk))
            error = live_state_error(
                status=state_status,
                error=self._fetch_error,
            )
            if error:
                status.append(
                    disabled_menu_item(
                        _("Error: {msg}").format(msg=error),
                        gtk=Gtk,
                    )
                )

        refresh: list[Gtk.MenuItem] = []
        if active:
            refresh_item = Gtk.MenuItem(label=_("Refresh Now"))
            refresh_item.connect("activate", lambda _w: self._fetch_async())
            refresh.append(refresh_item)

        show_temp = Gtk.CheckMenuItem(label=_("Show Temperature"))
        show_temp.set_active(self._show_temperature)
        show_temp.connect("toggled", self._on_toggle_temperature)

        display = [
            show_temp,
            radio_submenu(
                label=_("Temperature Unit"),
                choices=tuple(
                    (temperature_unit_label(unit), unit)
                    for unit in (TemperatureUnit.CELSIUS, TemperatureUnit.FAHRENHEIT)
                ),
                active_value=self._temperature_unit,
                on_selected=lambda widget, value: self._on_temperature_unit_selected(
                    widget=widget,
                    temperature_unit=value,
                ),
                gtk=Gtk,
            ),
        ]

        destructive: list[Gtk.MenuItem] = []
        if active and len(self._cities) > 1:
            remove = Gtk.MenuItem(
                label=_("Remove {city}").format(city=active.city_display)
            )
            remove.connect("activate", lambda _: self._remove_active_city())
            destructive.append(remove)

        return menu_sections(
            status=status,
            refresh=refresh,
            display=display,
            destructive=destructive,
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL, self._tick)
        if self._active_city:
            self._startup_fetch_timer_id = GLib.timeout_add_seconds(
                STARTUP_FETCH_DELAY_S,
                self._run_startup_fetch,
            )

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        super().stop()

    def _on_toggle_temperature(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_temperature = widget.get_active()
        self._save_prefs()
        self.present()

    def _on_temperature_unit_selected(
        self,
        *,
        widget: Gtk.RadioMenuItem,
        temperature_unit: TemperatureUnit,
    ) -> None:
        if not widget.get_active():
            return
        if temperature_unit == self._temperature_unit:
            return
        self._temperature_unit = temperature_unit
        self._save_prefs()
        self.present()

    def _show_city_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Search for the city"),
            modal=True,
            destroy_with_parent=True,
        )
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
        store = Gtk.ListStore(str, float, float)  # display, lat, lng
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

    def _tick(self) -> bool:
        if self._active_city:
            self._fetch_async()
        return True

    def _run_startup_fetch(self) -> bool:
        self._startup_fetch_timer_id = 0
        self._fetch_async()
        return False

    def _add_city(self, display: str, lat: float, lng: float) -> None:
        """Add a city or switch to it if it already exists."""
        for i, c in enumerate(self._cities):
            if c.lat == lat and c.lng == lng:
                self._active_index = i
                self._weather = None
                self._air_quality = None
                self._last_updated = None
                self._loading = False
                self._fetch_pending = False
                self._fetch_failed = False
                self._fetch_error = ""
                self._save_prefs()
                self._fetch_async()
                self.present()
                return
        self._cities.append(CityPref(city_display=display, lat=lat, lng=lng))
        self._active_index = len(self._cities) - 1
        self._weather = None
        self._air_quality = None
        self._last_updated = None
        self._loading = False
        self._fetch_pending = False
        self._fetch_failed = False
        self._fetch_error = ""
        self._save_prefs()
        self._fetch_async()
        self.present()

    def _remove_active_city(self) -> None:
        if len(self._cities) <= 1:
            return
        del self._cities[self._active_index]
        self._active_index = min(self._active_index, len(self._cities) - 1)
        self._weather = None
        self._air_quality = None
        self._last_updated = None
        self._loading = False
        self._fetch_pending = False
        self._fetch_failed = False
        self._fetch_error = ""
        self._save_prefs()
        self._fetch_async()
        self.present()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                cities=tuple(self._cities),
                active_index=self._active_index,
                show_temperature=self._show_temperature,
                temperature_unit=self._temperature_unit,
            )
        )

    def _fetch_async(self) -> None:
        """Fetch weather + air quality in a background thread."""
        active = self._active_city
        if not active:
            return
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        if self._loading:
            self._fetch_pending = True
            return

        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        lat = active.lat
        lng = active.lng
        self._loading = True
        self._fetch_pending = False
        self._fetch_failed = False
        self._fetch_error = ""
        self.present()

        def fetch() -> tuple[WeatherData | None, AirQualityData | None]:
            weather = fetch_weather(lat=lat, lng=lng)
            if weather is None:
                return None, None
            aqi = fetch_air_quality(lat=lat, lng=lng)
            return weather, aqi

        started = self._worker.run_guarded(
            key="weather-fetch",
            name="weather-fetch",
            fn=fetch,
            on_result=lambda result: self._on_fetch_result(
                request_id=request_id,
                weather=result[0],
                aqi=result[1],
            ),
            on_error=lambda exc: self._on_fetch_error(request_id=request_id, exc=exc),
        )
        if not started:
            self._fetch_pending = True

    def _on_fetch_result(
        self,
        request_id: int,
        weather: WeatherData | None,
        aqi: AirQualityData | None,
    ) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._loading = False
        self._fetch_failed = weather is None
        if weather is not None:
            self._weather = weather
            self._air_quality = aqi
            self._fetch_error = ""
            self._last_updated = dt.datetime.now(dt.timezone.utc)
        else:
            self._fetch_error = _("No weather data")
        self._present_or_run_pending_fetch()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._fetch_request_id:
            return False
        log.bind(action="fetch_error").debug("Weather fetch failed: %s", exc)
        self._loading = False
        self._fetch_failed = True
        self._fetch_error = str(exc) or exc.__class__.__name__
        self._present_or_run_pending_fetch()
        return False

    def _present_or_run_pending_fetch(self) -> None:
        if self._fetch_pending and self._active_city:
            self._fetch_pending = False
            self._fetch_async()
            return
        self.present()

    def _build_tooltip(self) -> str:
        active = self._active_city
        return build_tooltip(
            city_display=active.city_display if active else "",
            weather=self._weather,
            air_quality=self._air_quality,
            loading=self._loading,
            fetch_failed=self._fetch_failed,
            error=self._fetch_error,
            temperature_unit=self._temperature_unit,
            updated_at=self._last_updated,
            cadence_seconds=REFRESH_INTERVAL,
        )

    def _live_status(self):
        return resolve_live_status(
            has_data=self._weather is not None,
            loading=self._loading,
            error=self._fetch_error if self._fetch_failed else None,
            updated_at=self._last_updated,
            stale_after_seconds=REFRESH_INTERVAL * 2,
        )

    def _build_tooltip_widget(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        active = self._active_city

        if not active or not self._weather:
            label = Gtk.Label(label=self._build_tooltip())
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            box.pack_start(label, False, False, 0)
            return box

        weather = self._weather

        city = Gtk.Label()
        city.set_markup(f"<b>{GLib.markup_escape_text(active.city_display)}</b>")
        city.set_xalign(0.5)
        city.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        box.pack_start(city, False, False, 0)

        current = Gtk.Label(
            label=_("{temp}, {desc}").format(
                temp=format_temperature(
                    weather.temperature,
                    temperature_unit=self._temperature_unit,
                ),
                desc=weather.description,
            )
        )
        current.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.9))
        box.pack_start(current, False, False, 0)

        if self._air_quality:
            aqi_lbl = Gtk.Label(
                label=_("Air: {label}").format(label=self._air_quality.label)
            )
            aqi_lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.7))
            box.pack_start(aqi_lbl, False, False, 0)
            if self._air_quality.uv_index is not None:
                uv_lbl = Gtk.Label(
                    label=_("UV Index: {value}").format(
                        value=f"{self._air_quality.uv_index:.1f}"
                    )
                )
                uv_lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.7))
                box.pack_start(uv_lbl, False, False, 0)

        for day in weather.daily:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            icon = Gtk.Image.new_from_icon_name(
                wmo_icon_name(code=day.code),
                Gtk.IconSize.LARGE_TOOLBAR,
            )
            label = Gtk.Label(
                label=_("{date}: {temp_range}").format(
                    date=day.date,
                    temp_range=format_temperature_range(
                        low_celsius=day.temp_min,
                        high_celsius=day.temp_max,
                        temperature_unit=self._temperature_unit,
                    ),
                )
            )
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            row.pack_start(icon, False, False, 0)
            row.pack_start(label, False, False, 0)
            box.pack_start(row, False, False, 0)

        state_status = self._live_status()
        state_label = live_state_label(state_status)
        error = live_state_error(status=state_status, error=self._fetch_error)
        for text in (
            state_label,
            _("Error: {msg}").format(msg=error) if error else "",
        ):
            if not text:
                continue
            label = Gtk.Label(label=text)
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.72))
            box.pack_start(label, False, False, 0)

        details = [
            updated_label(self._last_updated),
            cadence_label(seconds=REFRESH_INTERVAL),
        ]
        for text in (line for line in details if line):
            label = Gtk.Label(label=text)
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 0.62))
            box.pack_start(label, False, False, 0)

        return box
