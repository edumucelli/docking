"""Weather applet -- shows current weather for a user-selected city.

City selection via autocomplete dialog (right-click -> Change City).
Weather data from Open-Meteo API with caching and retry (5 min interval).
Icon from GTK theme matching WMO weather code, with optional temperature
overlay rendered via Cairo.
"""

from __future__ import annotations

import threading
from functools import lru_cache
from typing import TYPE_CHECKING, Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import (
    Gdk,
    GdkPixbuf,
    Gio,
    GLib,
    Gtk,
    Pango,
    PangoCairo,
)  # noqa: E402

import cairo

from docking.applets.base import Applet, load_theme_icon
from docking.applets.weather.api import (
    REFRESH_INTERVAL,
    WeatherData,
    fetch_weather,
    wmo_icon_name,
)
from docking.applets.weather.cities import CityEntry, load_cities, search_cities
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger("weather")


@lru_cache(maxsize=1)
def _get_cities() -> tuple[CityEntry, ...]:
    """Load city database on first access (cached)."""
    return tuple(load_cities())


class WeatherApplet(Applet):
    """Shows current weather icon + temperature for a selected city.

    Prefs: city_display, lat, lng, show_temperature.
    Updates every REFRESH_INTERVAL seconds via background thread.
    Click opens Open-Meteo forecast in browser.
    """

    id = "weather"
    name = "Weather"
    icon_name = "weather-few-clouds"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._weather: WeatherData | None = None
        self._city_display: str = ""
        self._lat: float = 0.0
        self._lng: float = 0.0

        self._show_temperature: bool = True

        # Load saved prefs
        if config:
            prefs = config.applet_prefs.get("weather", {})
            self._city_display = prefs.get("city_display", "")
            self._lat = prefs.get("lat", 0.0)
            self._lng = prefs.get("lng", 0.0)
            self._show_temperature = prefs.get("show_temperature", True)

        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load weather icon with temperature overlay at bottom center."""
        icon_name = self._weather.icon_name if self._weather else "weather-few-clouds"

        if hasattr(self, "item"):
            self.item.name = self._build_tooltip()
            self.item.tooltip_builder = self._build_tooltip_widget

        # Load base icon
        base = load_theme_icon(icon_name, size)
        if not base or not self._weather or not self._show_temperature:
            return base

        # Composite icon + temperature text via Cairo
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)

        # Paint base icon
        Gdk.cairo_set_source_pixbuf(cr, base, 0, 0)
        cr.paint()

        # Draw temperature text at bottom center (outlined for readability)
        temp_text = f"{self._weather.temperature:.0f}°"
        font_size = max(1, int(size * 0.22))
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription(f"Sans Bold {font_size}px"))
        layout.set_text(temp_text, -1)

        _ink, logical = layout.get_pixel_extents()
        tx = (size - logical.width) / 2 - logical.x
        ty = size - logical.height - max(1, size * 0.02) - logical.y

        cr.move_to(tx, ty)
        PangoCairo.layout_path(cr, layout)
        cr.set_source_rgba(0, 0, 0, 0.8)
        cr.set_line_width(max(2.0, size * 0.05))
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke_preserve()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.fill()

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def on_clicked(self) -> None:
        """Open Open-Meteo forecast page in browser."""
        if not self._city_display:
            return
        url = (
            f"https://open-meteo.com/en/docs#latitude={self._lat}"
            f"&longitude={self._lng}"
            f"&current=temperature_2m,weather_code"
            f"&daily=weather_code,temperature_2m_max,temperature_2m_min"
        )
        try:
            Gio.AppInfo.launch_default_for_uri(url, None)
        except GLib.Error as e:
            _log.warning("Failed to open weather URL: %s", e)

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Return current city label + 'Change City' item that opens a dialog."""
        items: list[Gtk.MenuItem] = []

        # Current city label (if set)
        if self._city_display:
            label = self._city_display
            if self._weather:
                label += f": {self._weather.temperature:.0f}°C"
            header = Gtk.MenuItem(label=label)
            header.set_sensitive(False)
            items.append(header)

        show_temp = Gtk.CheckMenuItem(label="Show Temperature")
        show_temp.set_active(self._show_temperature)
        show_temp.connect("toggled", self._on_toggle_temperature)
        items.append(show_temp)

        change = Gtk.MenuItem(label="Change City...")
        change.connect("activate", lambda _: self._show_city_dialog())
        items.append(change)

        return items

    def _on_toggle_temperature(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_temperature = widget.get_active()
        self._save_prefs()
        self.refresh_icon()

    def _show_city_dialog(self) -> None:
        """Open a dialog with a search entry + autocomplete for city selection."""
        dialog = Gtk.Dialog(
            title="Search for the city",
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dialog.set_default_size(350, -1)
        dialog.set_position(Gtk.WindowPosition.MOUSE)

        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Type city name...")
        box.pack_start(entry, False, False, 0)

        # Autocomplete with city database
        completion = Gtk.EntryCompletion()
        store = Gtk.ListStore(str, float, float)  # display, lat, lng
        completion.set_model(store)
        completion.set_text_column(0)
        completion.set_minimum_key_length(2)

        def on_changed(entry: Gtk.Entry) -> None:
            text = entry.get_text()
            store.clear()
            if len(text) >= 2:
                for city in search_cities(text, _get_cities(), limit=10):
                    store.append([city.display, city.lat, city.lng])

        def on_match_selected(
            _completion: Gtk.EntryCompletion,
            model: Gtk.TreeModel,
            tree_iter: Gtk.TreeIter,
        ) -> bool:
            display = model.get_value(tree_iter, 0)
            lat = model.get_value(tree_iter, 1)
            lng = model.get_value(tree_iter, 2)
            self._select_city(display, lat, lng)
            dialog.destroy()
            return True

        entry.connect("changed", on_changed)
        completion.connect("match-selected", on_match_selected)
        entry.set_completion(completion)

        dialog.show_all()
        entry.grab_focus()

    def start(self, notify: Callable[[], None]) -> None:
        """Start 30-minute weather polling timer; fetch immediately."""
        super().start(notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL, self._tick)
        # Fetch immediately if a city is configured
        if self._city_display:
            self._fetch_async()

    def stop(self) -> None:
        """Stop the polling timer."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def _tick(self) -> bool:
        """Timer callback: fetch weather in background."""
        if self._city_display:
            self._fetch_async()
        return True

    def _select_city(self, display: str, lat: float, lng: float) -> None:
        """Handle city selection from autocomplete."""
        self._city_display = display
        self._lat = lat
        self._lng = lng
        self._save_prefs()
        self._fetch_async()

    def _save_prefs(self) -> None:
        self.save_prefs(
            {
                "city_display": self._city_display,
                "lat": self._lat,
                "lng": self._lng,
                "show_temperature": self._show_temperature,
            }
        )

    def _fetch_async(self) -> None:
        """Fetch weather in a background thread to avoid blocking GTK."""

        def worker() -> None:
            data = fetch_weather(self._lat, self._lng)
            GLib.idle_add(self._on_weather_result, data)

        threading.Thread(target=worker, daemon=True).start()

    def _on_weather_result(self, data: WeatherData | None) -> bool:
        """Called on main thread with weather data."""
        self._weather = data
        self.refresh_icon()
        return False  # remove from idle

    def _build_tooltip(self) -> str:
        """Build multi-line tooltip with current + daily forecast."""
        if not self._city_display:
            return "Weather (no city selected)"
        if not self._weather:
            return f"{self._city_display}: loading..."

        w = self._weather
        lines = [f"{self._city_display}: {w.temperature:.0f}°C, {w.description}"]
        for day in w.daily:
            lines.append(
                f"{day.date}: {day.temp_min:.0f}/{day.temp_max:.0f}°C, {day.description}"
            )
        return "\n".join(lines)

    def _build_tooltip_widget(self) -> Gtk.Box:
        """Build tooltip widget with weather icons for each forecast day."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        if not self._city_display or not self._weather:
            label = Gtk.Label(label=self._build_tooltip())
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            box.pack_start(label, False, False, 0)
            return box

        w = self._weather
        header = Gtk.Label(
            label=f"{self._city_display}: {w.temperature:.0f}°C, {w.description}"
        )
        header.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        box.pack_start(header, False, False, 0)

        for day in w.daily:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            icon = Gtk.Image.new_from_icon_name(
                wmo_icon_name(day.code), Gtk.IconSize.LARGE_TOOLBAR
            )
            label = Gtk.Label(
                label=f"{day.date}: {day.temp_min:.0f}/{day.temp_max:.0f}°C"
            )
            label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            row.pack_start(icon, False, False, 0)
            row.pack_start(label, False, False, 0)
            box.pack_start(row, False, False, 0)

        return box
