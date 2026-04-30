"""GTK lifecycle glue for the Thermals applet."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.freshness import cadence_label
from docking.applets.live_state import (
    live_state_error,
    live_state_label,
    resolve_live_status,
)
from docking.applets.menu import disabled_menu_item, menu_sections, radio_submenu
from docking.applets.thermals import meta
from docking.applets.thermals.render import render_icon
from docking.applets.thermals.state import (
    REFRESH_INTERVAL_S,
    STARTUP_FETCH_DELAY_S,
    TemperatureUnit,
    ThermalSnapshot,
    build_tooltip,
    format_rpm,
    format_temperature,
    prefs_from_mapping,
    prefs_payload,
    read_thermal_snapshot,
    reading_label,
    temperature_unit_label,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="thermals"), applet_id=meta.id)


class ThermalsApplet(Applet):
    """Show the hottest lm-sensors reading and fastest fan RPM."""

    id = meta.id
    name = _("Thermals")
    icon_name = "utilities-system-monitor"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._snapshot: ThermalSnapshot | None = None
        self._last_updated: dt.datetime | None = None
        self._loading = False
        self._last_error = ""
        self._timer_id = 0
        self._startup_fetch_timer_id = 0
        self._request_id = 0
        self._worker = BackgroundWorker(logger=log)
        prefs = prefs_from_mapping(
            config.applet_prefs.get(meta.id, {}) if config else None
        )
        self._temperature_unit = prefs.temperature_unit
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(
            size=size,
            snapshot=self._snapshot,
            loading=self._loading,
            error=bool(self._last_error),
            temperature_unit=self._temperature_unit,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(
            snapshot=self._snapshot,
            loading=self._loading,
            error=self._last_error,
            temperature_unit=self._temperature_unit,
            updated_at=self._last_updated,
            cadence_seconds=REFRESH_INTERVAL_S,
        )

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []
        if self._snapshot is not None:
            if not self._snapshot.available:
                status.append(
                    disabled_menu_item(_("lm-sensors not installed"), gtk=Gtk)
                )
            elif self._snapshot.error:
                status.append(disabled_menu_item(self._snapshot.error, gtk=Gtk))
            else:
                if self._snapshot.hottest is not None:
                    hot = self._snapshot.hottest
                    status.append(
                        disabled_menu_item(
                            _("Hot: {label} {temp}").format(
                                label=reading_label(hot),
                                temp=format_temperature(
                                    hot.celsius,
                                    temperature_unit=self._temperature_unit,
                                ),
                            ),
                            gtk=Gtk,
                        )
                    )
                if self._snapshot.fan is not None:
                    fan = self._snapshot.fan
                    status.append(
                        disabled_menu_item(
                            _("Fan: {label} {rpm}").format(
                                label=reading_label(fan),
                                rpm=format_rpm(fan.rpm),
                            ),
                            gtk=Gtk,
                        )
                    )

        state_status = self._live_status()
        state_label = live_state_label(state_status)
        if state_label:
            status.append(disabled_menu_item(state_label, gtk=Gtk))
        error = live_state_error(status=state_status, error=self._last_error)
        if error:
            status.append(
                disabled_menu_item(_("Error: {msg}").format(msg=error), gtk=Gtk)
            )

        status.append(
            disabled_menu_item(
                cadence_label(seconds=REFRESH_INTERVAL_S, verb=_("Samples")),
                gtk=Gtk,
            )
        )

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._fetch_async())

        display = [
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
            )
        ]

        return menu_sections(
            status=status,
            refresh=[refresh],
            display=display,
            gtk=Gtk,
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._timer_id = GLib.timeout_add_seconds(REFRESH_INTERVAL_S, self._tick)
        self._startup_fetch_timer_id = GLib.timeout_add_seconds(
            STARTUP_FETCH_DELAY_S,
            self._run_startup_fetch,
        )

    def stop(self) -> None:
        for attr in ("_timer_id", "_startup_fetch_timer_id"):
            timer_id = getattr(self, attr, 0)
            if timer_id:
                GLib.source_remove(timer_id)
                setattr(self, attr, 0)
        super().stop()

    def _tick(self) -> bool:
        self._fetch_async()
        return True

    def _run_startup_fetch(self) -> bool:
        self._startup_fetch_timer_id = 0
        self._fetch_async()
        return False

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

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs=prefs_payload(temperature_unit=self._temperature_unit),
        )

    def _fetch_async(self) -> None:
        if self._loading:
            return
        if self._startup_fetch_timer_id:
            GLib.source_remove(self._startup_fetch_timer_id)
            self._startup_fetch_timer_id = 0
        self._request_id += 1
        request_id = self._request_id
        self._loading = self._snapshot is None
        self._last_error = ""
        if self._loading:
            self.present()

        self._worker.run(
            name="thermals-fetch",
            fn=read_thermal_snapshot,
            on_result=lambda snapshot: self._on_fetch_result(
                request_id=request_id,
                snapshot=snapshot,
            ),
            on_error=lambda exc: self._on_fetch_error(
                request_id=request_id,
                exc=exc,
            ),
        )

    def _on_fetch_result(
        self,
        *,
        request_id: int,
        snapshot: ThermalSnapshot,
    ) -> bool:
        if request_id != self._request_id:
            return False
        self._loading = False
        if (
            (snapshot.available and not snapshot.error)
            or self._snapshot is None
            or self._snapshot.error
        ):
            self._snapshot = snapshot
            self._last_error = ""
        else:
            self._last_error = snapshot.error or _("Thermal readings unavailable")
        self._last_updated = dt.datetime.now(dt.timezone.utc)
        self.present()
        return False

    def _on_fetch_error(self, *, request_id: int, exc: Exception) -> bool:
        if request_id != self._request_id:
            return False
        self._loading = False
        error = str(exc) or exc.__class__.__name__
        if self._snapshot is None or self._snapshot.error:
            self._snapshot = ThermalSnapshot(available=True, error=error)
            self._last_error = ""
        else:
            self._last_error = error
        self._last_updated = dt.datetime.now(dt.timezone.utc)
        log.bind(action="fetch_error").debug("Thermals fetch failed: %s", exc)
        self.present()
        return False

    def _live_status(self):
        snapshot_error = self._snapshot.error if self._snapshot is not None else ""
        return resolve_live_status(
            has_data=(
                self._snapshot is not None
                and self._snapshot.available
                and not self._snapshot.error
            ),
            loading=self._loading,
            error=self._last_error or snapshot_error,
            updated_at=self._last_updated,
            stale_after_seconds=REFRESH_INTERVAL_S * 2,
        )
