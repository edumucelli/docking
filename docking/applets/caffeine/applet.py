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

"""GTK lifecycle glue for the Caffeine applet."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

from docking.applets.platform import AppletPlatformServices

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import PlatformServicesApplet
from docking.applets.caffeine import meta
from docking.applets.caffeine.inhibit import (
    InhibitionResult,
    InhibitionSession,
    Inhibitor,
)
from docking.applets.caffeine.render import render_icon
from docking.applets.caffeine.state import (
    DURATION_PRESETS,
    activate,
    deactivate,
    duration_label,
    has_timer,
    prefs_from_state,
    set_duration,
    state_from_prefs,
    status_text,
    tick,
    tooltip_text,
)
from docking.applets.menu import disabled_menu_item, menu_sections, radio_menu_items
from docking.i18n import _
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

log = get_logger(name="caffeine")


class CaffeineApplet(PlatformServicesApplet):
    """Toggle that keeps the session awake by inhibiting idle and sleep."""

    id = meta.id
    name = _("Caffeine")
    icon_name = "caffeine"

    def __init__(
        self,
        icon_size: int,
        config: Config,
        inhibitor: Inhibitor | None = None,
        *,
        platform_services: AppletPlatformServices,
    ) -> None:
        self._platform = platform_services
        prefs = config.applet_prefs.get(meta.id, {})
        self._data = state_from_prefs(prefs=prefs)
        self._inhibitor = (
            inhibitor if inhibitor is not None else self._platform.backends.caffeine()
        )
        self._session = InhibitionSession(self._inhibitor, self._on_inhibition_done)
        self._requested = False
        self._request_pending = False
        self._error = False
        self._stopped = False
        self._timer_id: int = 0

        super().__init__(icon_size, config, platform_services=platform_services)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        return render_icon(size=size, state=self._data)

    def refresh_tooltip(self) -> None:
        self.item.name = tooltip_text(state=self._data)
        if self._request_pending:
            self.item.name += "\n" + _("Loading...")
        elif self._error:
            self.item.name += "\n" + _("Unavailable")

    def start(self, notify: Callable[[], None]) -> None:
        self._stopped = False
        self._session.start()
        super().start(notify)

    def stop(self) -> None:
        self._stopped = True
        self._requested = self._request_pending = False
        self._cancel_timer()
        self._session.stop()
        self._data = deactivate(self._data)
        super().stop()

    # -- Interaction ---------------------------------------------------------

    def on_clicked(self) -> None:
        if not self._stopped:
            self._requested = not self._requested
            self._apply()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status = [disabled_menu_item(status_text(state=self._data))]
        if self._request_pending:
            status.append(disabled_menu_item(_("Loading...")))
        elif self._error:
            status.append(disabled_menu_item(_("Unavailable")))
        display: list[Gtk.MenuItem] = [disabled_menu_item(_("Turn off after"))]
        display.extend(
            radio_menu_items(
                choices=tuple(
                    (duration_label(minutes=minutes), minutes)
                    for minutes in DURATION_PRESETS
                ),
                active_value=self._data.duration_min,
                on_selected=lambda _widget, value: self._set_duration(minutes=value),
                gtk=Gtk,
            )
        )
        return menu_sections(status=status, display=display, gtk=Gtk)

    # -- Internals -----------------------------------------------------------

    def _set_duration(self, minutes: int) -> None:
        if self._stopped:
            return
        self._data = set_duration(state=self._data, minutes=minutes)
        self.save_prefs(prefs=prefs_from_state(state=self._data))
        self._sync_timer()
        self.present()

    def _apply(self) -> None:
        self._error = False
        self._request_pending = True
        self._session.request(self._requested)
        self.present()

    def _on_inhibition_done(self, result: InhibitionResult) -> bool:
        if self._stopped or result.version != self._session.version:
            return False
        self._request_pending = False
        self._requested = result.active
        self._error = not result.succeeded
        self._data = activate(self._data) if result.active else deactivate(self._data)
        self._sync_timer()
        self.present()
        return False

    def _sync_timer(self) -> None:
        if has_timer(state=self._data) and not self._timer_id:
            self._timer_id = GLib.timeout_add_seconds(1, self._tick)
        elif not has_timer(state=self._data):
            self._cancel_timer()

    def _cancel_timer(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0

    def _tick(self) -> bool:
        if self._stopped:
            return False
        updated = tick(state=self._data)
        if self._data.active and not updated.active:
            self._timer_id = 0
            self._data = replace(self._data, remaining=0)
            self._requested = False
            self._apply()
            return False
        self._data = updated
        self.present()
        return True
