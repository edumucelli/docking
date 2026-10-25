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

"""GTK lifecycle glue for Cam Shield applet."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.camshield import meta
from docking.applets.camshield.backend import create_backend
from docking.applets.camshield.render import render_icon
from docking.applets.camshield.state import (
    DEFAULT_POLL_INTERVAL_S,
    CamshieldState,
    build_tooltip,
    holder_label,
)
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="camshield"), applet_id=meta.id)

_PULSE_INTERVAL_MS = 60  # Match Desk Presence: smooth enough without taxing CPU.
_PULSE_PERIOD_MS = 1800


class CamshieldApplet(Applet):
    """Show when any process is holding a camera device."""

    id = meta.id
    name = _("Cam Shield")
    icon_name = "camera-web"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._backend = create_backend()
        self._action_in_progress = False
        self._state = CamshieldState(available=False, active=False)
        self._worker = BackgroundWorker(logger=log)
        self._generation = 0
        self._stopped = False
        self._timer_id: int = 0
        self._pulse_timer_id: int = 0
        self._pulse_phase: float = 0.0
        super().__init__(icon_size=icon_size, config=config)
        self.present()
        self._refresh_now()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        active = self._state.active
        phase = self._pulse_phase if active else None
        return render_icon(
            size=size,
            available=self._state.available,
            active=bool(active),
            pulse_phase=phase,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        if self._stopped:
            self._backend = create_backend()
            self._state = CamshieldState(False, False)
            self._action_in_progress = False
        self._stopped = False
        self._generation += 1
        super().start(notify=notify)
        GLib.idle_add(self._refresh_once)
        self._timer_id = GLib.timeout_add_seconds(
            DEFAULT_POLL_INTERVAL_S,
            self._tick,
        )
        self._ensure_pulse_timer()

    def stop(self) -> None:
        self._stopped = True
        self._generation += 1
        self._backend.stop()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
        super().stop()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []
        if not self._state.available:
            status.append(
                disabled_menu_item(
                    _("Camera state unavailable")
                    if self._state.error
                    else _("No camera devices found"),
                    gtk=Gtk,
                )
            )
        elif self._state.active is None:
            status.append(disabled_menu_item(_("Camera activity unknown"), gtk=Gtk))
        elif not self._state.active:
            status.append(disabled_menu_item(_("Camera idle"), gtk=Gtk))
        else:
            status.append(disabled_menu_item(_("Camera active"), gtk=Gtk))
            for holder in self._state.holders:
                status.append(disabled_menu_item(holder_label(holder), gtk=Gtk))
        if self._state.locked:
            status.insert(0, disabled_menu_item(_("Camera locked"), gtk=Gtk))

        lock = Gtk.MenuItem(label=_("Lock Camera"))
        lock.set_sensitive(self._state.available and self._backend.lock_available())
        lock.connect("activate", lambda _w: self._run_action("lock"))

        unlock = Gtk.MenuItem(label=_("Unlock Camera"))
        unlock.set_sensitive(self._backend.unlock_available())
        unlock.connect("activate", lambda _w: self._run_action("unlock"))

        if not self._backend.controls_available():
            status.append(disabled_menu_item(_("Camera locking unavailable"), gtk=Gtk))

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._refresh_now())
        settings = []
        if self._backend.settings_available():
            item = Gtk.MenuItem(label=_("Camera Settings"))
            item.connect("activate", lambda _w: self._backend.open_settings())
            settings.append(item)
        return menu_sections(
            status=status,
            primary=[lock, unlock],
            refresh=[refresh],
            settings=settings,
            gtk=Gtk,
        )

    def _refresh_once(self) -> bool:
        self._refresh_now()
        return False

    def _refresh_now(self) -> None:
        if self._stopped or self._action_in_progress:
            return
        generation, backend = self._generation, self._backend
        self._worker.run_guarded(
            key="camera-probe",
            name="camera-probe",
            fn=lambda: (
                backend.get_state()
                if self._request_is_current(backend, generation)
                else CamshieldState(False, False)
            ),
            on_result=lambda state: (
                self._on_state_result(state, generation)
                if self._request_is_current(backend, generation)
                else None
            ),
            on_error=lambda _error: (
                self._on_state_result(
                    CamshieldState(False, None, error=True), generation
                )
                if self._request_is_current(backend, generation)
                else None
            ),
        )

    def _on_state_result(self, state: CamshieldState, generation: int) -> None:
        if self._stopped or generation != self._generation:
            return
        self._state = state
        self._ensure_pulse_timer()
        self.present()

    def _tick(self) -> bool:
        if self._stopped:
            return False
        self._refresh_now()
        return True

    def _ensure_pulse_timer(self) -> None:
        """Run the red-dot pulse only while a camera device is active."""
        should_pulse = (
            not self._stopped and self._notify is not None and self._state.active
        )
        if should_pulse and not self._pulse_timer_id:
            self._pulse_timer_id = GLib.timeout_add(
                _PULSE_INTERVAL_MS,
                self._pulse_tick,
            )
        elif not should_pulse and self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
            self._pulse_phase = 0.0

    def _pulse_tick(self) -> bool:
        if self._stopped:
            return False
        self._pulse_phase = (
            self._pulse_phase + _PULSE_INTERVAL_MS / _PULSE_PERIOD_MS
        ) % 1.0
        # Repaint the icon only; skip tooltip/menu state churn between probes.
        self.item.icon = self.create_icon(size=self._icon_size)
        if self._notify:
            self._notify()
        return True

    def _request_is_current(self, backend, generation: int) -> bool:
        return (
            not self._stopped
            and backend is self._backend
            and generation == self._generation
        )

    def _run_action(self, action: str) -> None:
        if (
            self._stopped
            or self._action_in_progress
            or action not in ("lock", "unlock")
        ):
            return
        backend = self._backend
        if not (
            backend.lock_available() if action == "lock" else backend.unlock_available()
        ):
            return
        self._generation += 1
        generation = self._generation
        self._action_in_progress = True

        def task():
            if not self._request_is_current(backend, generation):
                return None
            if action == "lock":
                backend.lock()
            else:
                backend.unlock()
            return backend.get_state()

        def completed(state):
            if not self._request_is_current(backend, generation):
                return False
            self._action_in_progress = False
            self._generation += 1
            if state is not None:
                self._on_state_result(state, self._generation)
            return False

        self._worker.run(
            name="camera-" + action,
            fn=task,
            on_result=completed,
            on_error=lambda _error: completed(CamshieldState(False, None, error=True)),
        )
