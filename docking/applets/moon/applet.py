"""GTK lifecycle for Moon phase applet.

This applet is a spiritual successor to the Moon applet originally written
for Cairo-Dock by Eduardo Mucelli (circa 2012). The original fetched moon
data from briancasey.org using SGMLParser and displayed phase images as
sub-icons. This version renders the moon in Cairo and fetches from the
same website - coming full circle after over a decade.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.base import Applet
from docking.applets.moon import meta
from docking.applets.moon.render import create_icon
from docking.applets.moon.state import MoonData, fetch_moon, phase_name
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="moon"), applet_id=meta.id)

# Refresh every 6 hours (moon phase changes slowly)
REFRESH_INTERVAL = 6 * 60 * 60


class MoonApplet(Applet):
    """Moon phase display - fetches data from briancasey.org.

    Shows current moon phase as a Cairo-rendered disc with illumination.
    Click to refresh. Tooltip shows phase name and illumination percentage.

    The data source (briancasey.org/artifacts/astro/moon.cgi) is the same
    website used by the original Cairo-Dock Moon applet from 2012.
    """

    id = meta.id
    name = _("Moon")
    icon_name = "weather-clear-night"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._moon: MoonData | None = None
        self._show_phase = True
        self._timer_id: int = 0
        self._worker = BackgroundWorker(logger=log)

        if config:
            prefs = config.applet_prefs.get(meta.id, {})
            self._show_phase = prefs.get("show_phase", True)

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int):
        if not self._moon:
            return create_icon(
                size=size, illumination=0.5, label="..." if self._show_phase else None
            )
        waning = (
            "after full" in self._moon.description.lower()
            or "before last" in self._moon.description.lower()
            or "before new" in self._moon.description.lower()
            or "after last" in self._moon.description.lower()
        )
        label = None
        if self._show_phase:
            name = phase_name(
                illumination=self._moon.illumination,
                description=self._moon.description,
            )
            label = name
        return create_icon(
            size=size,
            illumination=self._moon.illumination,
            waning=waning,
            label=label,
        )

    def refresh_tooltip(self) -> None:
        if not self._moon:
            self.item.name = _("Moon (loading...)")
            return
        name = phase_name(
            illumination=self._moon.illumination,
            description=self._moon.description,
        )
        pct = int(self._moon.illumination * 100)
        lines = [name, _("Illumination: {pct}%").format(pct=pct)]
        if self._moon.description:
            lines.append(self._moon.description.capitalize())
        self.item.name = "\n".join(lines)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL, self._tick)
        self._fetch_async()

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        """Refresh on click."""
        self._fetch_async()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        show = Gtk.CheckMenuItem(label=_("Show Phase Name"))
        show.set_active(self._show_phase)
        show.connect("toggled", self._on_toggle_phase)
        items.append(show)

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _: self._fetch_async())
        items.append(refresh)

        return items

    def _on_toggle_phase(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_phase = widget.get_active()
        self.save_prefs(prefs={"show_phase": self._show_phase})
        self.present()

    def _tick(self) -> bool:
        self._fetch_async()
        return True

    def _fetch_async(self) -> None:
        self._worker.run_guarded(
            key="fetch",
            name="moon-fetch",
            fn=fetch_moon,
            on_result=self._on_result,
        )

    def _on_result(self, data: MoonData | None) -> bool:
        if data:
            self._moon = data
            self.present()
        return False
