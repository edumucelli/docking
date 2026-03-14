"""GTK lifecycle glue for weather applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.identity import AppletId
from docking.applets.weather.api import (
    REFRESH_INTERVAL,
    AirQualityData,
    WeatherData,
    wmo_icon_name,
)
from docking.applets.weather.cities import search_cities
from docking.applets.weather.render import create_icon
from docking.applets.weather.state import (
    build_forecast_url,
    build_tooltip,
    cached_cities,
    menu_header_label,
    prefs_from_mapping,
    prefs_payload,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

_log = with_context(get_logger(name="weather"), applet_id=str(AppletId.WEATHER))

CITY_DIALOG_WIDTH_PX = 350
DIALOG_CONTENT_SPACING_PX = 8
DIALOG_HORIZONTAL_MARGIN_PX = 12
DIALOG_VERTICAL_MARGIN_PX = 8
CITY_SEARCH_MIN_CHARS = 2
CITY_SEARCH_RESULT_LIMIT = 10


class WeatherApplet(Applet):
    """Shows current weather icon + temperature for a selected city."""

    id = AppletId.WEATHER
    name = _("Weather")
    icon_name = "weather-few-clouds"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._fetch_request_id: int = 0
        self._weather: WeatherData | None = None
        self._air_quality: AirQualityData | None = None
        self._worker = BackgroundWorker(logger=_log)

        prefs = prefs_from_mapping(
            config.applet_prefs.get("weather", {}) if config else None
        )
        self._city_display = prefs.city_display
        self._lat = prefs.lat
        self._lng = prefs.lng
        self._show_temperature = prefs.show_temperature

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return create_icon(
            size=size,
            weather=self._weather,
            show_temperature=self._show_temperature,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = self._build_tooltip()
        self.item.tooltip_builder = self._build_tooltip_widget

    def on_clicked(self) -> None:
        if not self._city_display:
            return
        url = build_forecast_url(lat=self._lat, lng=self._lng)
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as exc:
            _log.bind(action="open_url").warning(
                f"Failed to open weather URL: {exc}",
            )

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        if self._city_display:
            header = Gtk.MenuItem(
                label=menu_header_label(
                    city_display=self._city_display,
                    weather=self._weather,
                )
            )
            header.set_sensitive(False)
            items.append(header)

        show_temp = Gtk.CheckMenuItem(label=_("Show Temperature"))
        show_temp.set_active(self._show_temperature)
        show_temp.connect("toggled", self._on_toggle_temperature)
        items.append(show_temp)

        change = Gtk.MenuItem(label=_("Change City..."))
        change.connect("activate", lambda _: self._show_city_dialog())
        items.append(change)
        return items

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL, self._tick)
        if self._city_display:
            self._fetch_async()

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def _on_toggle_temperature(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_temperature = widget.get_active()
        self._save_prefs()
        self.present()

    def _show_city_dialog(self) -> None:
        dialog = Gtk.Dialog(
            title=_("Search for the city"),
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.set_default_size(CITY_DIALOG_WIDTH_PX, -1)
        dialog.set_position(Gtk.WindowPosition.MOUSE)

        box = dialog.get_content_area()
        box.set_spacing(DIALOG_CONTENT_SPACING_PX)
        box.set_margin_start(DIALOG_HORIZONTAL_MARGIN_PX)
        box.set_margin_end(DIALOG_HORIZONTAL_MARGIN_PX)
        box.set_margin_top(DIALOG_VERTICAL_MARGIN_PX)
        box.set_margin_bottom(DIALOG_VERTICAL_MARGIN_PX)

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
            self._select_city(display=display, lat=lat, lng=lng)
            dialog.destroy()
            return True

        entry.connect("changed", on_changed)
        completion.connect("match-selected", on_match_selected)
        entry.set_completion(completion)

        dialog.show_all()
        entry.grab_focus()

    def _tick(self) -> bool:
        if self._city_display:
            self._fetch_async()
        return True

    def _select_city(self, display: str, lat: float, lng: float) -> None:
        self._city_display = display
        self._lat = lat
        self._lng = lng
        self._save_prefs()
        self._fetch_async()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(
                city_display=self._city_display,
                lat=self._lat,
                lng=self._lng,
                show_temperature=self._show_temperature,
            )
        )

    def _fetch_async(self) -> None:
        """Fetch weather + air quality in a background thread."""
        import docking.applets.weather as weather_pkg

        self._fetch_request_id += 1
        request_id = self._fetch_request_id
        lat = self._lat
        lng = self._lng

        def fetch() -> tuple[WeatherData | None, AirQualityData | None]:
            weather = weather_pkg.fetch_weather(lat=lat, lng=lng)
            aqi = weather_pkg.fetch_air_quality(lat=lat, lng=lng)
            return weather, aqi

        self._worker.run(
            name="weather-fetch",
            fn=fetch,
            on_result=lambda result: self._on_fetch_result(
                request_id=request_id,
                weather=result[0],
                aqi=result[1],
            ),
        )

    def _on_fetch_result(
        self,
        request_id: int,
        weather: WeatherData | None,
        aqi: AirQualityData | None,
    ) -> bool:
        if request_id != self._fetch_request_id:
            return False
        self._weather = weather
        self._air_quality = aqi
        self.present()
        return False

    def _build_tooltip(self) -> str:
        return build_tooltip(
            city_display=self._city_display,
            weather=self._weather,
            air_quality=self._air_quality,
        )

    def _build_tooltip_widget(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        if not self._city_display or not self._weather:
            label = Gtk.Label(label=self._build_tooltip())
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            box.pack_start(label, False, False, 0)
            return box

        weather = self._weather

        city = Gtk.Label()
        city.set_markup(f"<b>{GLib.markup_escape_text(self._city_display)}</b>")
        city.set_xalign(0.5)
        city.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        box.pack_start(city, False, False, 0)

        current = Gtk.Label(
            label=_("{temp}°C, {desc}").format(
                temp=f"{weather.temperature:.0f}", desc=weather.description
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

        for day in weather.daily:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            icon = Gtk.Image.new_from_icon_name(
                wmo_icon_name(code=day.code),
                Gtk.IconSize.LARGE_TOOLBAR,
            )
            label = Gtk.Label(
                label=_("{date}: {min_temp}/{max_temp}°C").format(
                    date=day.date,
                    min_temp=f"{day.temp_min:.0f}",
                    max_temp=f"{day.temp_max:.0f}",
                )
            )
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            row.pack_start(icon, False, False, 0)
            row.pack_start(label, False, False, 0)
            box.pack_start(row, False, False, 0)

        return box
