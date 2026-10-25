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

"""GTK lifecycle glue for Mic Shield."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections
from docking.applets.micshield import meta
from docking.applets.micshield.backend import create_backend
from docking.applets.micshield.render import render_icon
from docking.applets.micshield.state import (
    DEFAULT_POLL_INTERVAL_S,
    MicShieldState,
    build_tooltip,
    mute_status,
    stream_label,
)
from docking.applets.worker import BackgroundWorker
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config

log = with_context(get_logger(name="micshield"), applet_id=meta.id)

_PULSE_INTERVAL_MS = 60
_PULSE_PERIOD_MS = 1800


class MicShieldApplet(Applet):
    """Microphone mute toggle and active-capture privacy indicator."""

    id = meta.id
    name = _("Mic Shield")
    icon_name = "audio-input-microphone"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._backend = create_backend()
        self._state = MicShieldState(False, False, False)
        self._stopped = False
        self._action_in_progress = False
        self._timer_id: int = 0
        self._pulse_timer_id: int = 0
        self._pulse_phase: float = 0.0
        self._poll_generation = 0
        self._worker = BackgroundWorker(logger=log)
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        phase = self._pulse_phase if self._state.active else None
        return render_icon(
            size=size,
            available=self._state.available,
            muted=bool(self._state.muted),
            active=bool(self._state.active),
            pulse_phase=phase,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        if self._stopped:
            self._backend = create_backend()
            self._state = MicShieldState(False, False, False)
            self._action_in_progress = False
        self._stopped = False
        self._poll_generation += 1
        super().start(notify=notify)
        GLib.idle_add(self._refresh_once)
        self._timer_id = GLib.timeout_add_seconds(
            DEFAULT_POLL_INTERVAL_S,
            self._tick,
        )
        self._ensure_pulse_timer()

    def stop(self) -> None:
        self._stopped = True
        self._poll_generation += 1
        self._backend.stop()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
        super().stop()

    def on_clicked(self) -> None:
        self._toggle_mute()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        status: list[Gtk.MenuItem] = []
        if not self._state.available:
            status.append(
                disabled_menu_item(
                    _("Microphone state unavailable")
                    if self._state.error
                    else _("No microphone source found"),
                    gtk=Gtk,
                )
            )
        else:
            status.append(
                disabled_menu_item(
                    mute_status(self._state),
                    gtk=Gtk,
                )
            )

            if self._state.active:
                status.append(disabled_menu_item(_("Microphone active"), gtk=Gtk))
                for stream in self._state.streams:
                    status.append(disabled_menu_item(stream_label(stream), gtk=Gtk))
            elif self._state.active is None:
                status.append(
                    disabled_menu_item(_("Microphone activity unknown"), gtk=Gtk)
                )
            else:
                status.append(disabled_menu_item(_("Microphone idle"), gtk=Gtk))

        mute_label = (
            _("Unmute Microphone") if self._state.muted else _("Mute Microphone")
        )
        mute = Gtk.MenuItem(label=mute_label)
        mute.set_sensitive(self._state.available)
        mute.connect(
            "activate", lambda _w, value=not self._state.muted: self._set_muted(value)
        )

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._refresh_now())
        settings = []
        if self._backend.settings_available():
            item = Gtk.MenuItem(label=_("Microphone Settings"))
            item.connect("activate", lambda _w: self._backend.open_settings())
            settings.append(item)
        return menu_sections(
            status=status,
            primary=[mute],
            refresh=[refresh],
            settings=settings,
            gtk=Gtk,
        )

    def _refresh_once(self) -> bool:
        self._refresh_now()
        return False

    def _request_is_current(self, backend, generation: int) -> bool:
        return (
            not self._stopped
            and backend is self._backend
            and generation == self._poll_generation
        )

    def _refresh_now(self) -> None:
        if self._stopped or self._action_in_progress:
            return
        backend, generation = self._backend, self._poll_generation
        self._worker.run_guarded(
            key="micshield-poll",
            name="micshield-poll",
            fn=lambda: (
                backend.get_state()
                if self._request_is_current(backend, generation)
                else MicShieldState(False, False, False)
            ),
            on_result=lambda state: (
                self._on_probe_result(state)
                if self._request_is_current(backend, generation)
                else False
            ),
            on_error=lambda _error: (
                self._on_probe_result(MicShieldState(False, None, None, error=True))
                if self._request_is_current(backend, generation)
                else False
            ),
        )

    def _tick(self) -> bool:
        if self._stopped:
            return False
        self._refresh_now()
        return True

    def _on_probe_result(self, state: MicShieldState) -> bool:
        if self._stopped:
            return False
        self._state = state
        self._ensure_pulse_timer()
        self.present()
        return False

    def _toggle_mute(self) -> None:
        self._perform_mute(None)

    def _set_muted(self, muted: bool) -> None:
        self._perform_mute(muted)

    def _perform_mute(self, muted: bool | None) -> None:
        if self._stopped or not self._state.available or self._action_in_progress:
            return
        self._poll_generation += 1
        backend, generation = self._backend, self._poll_generation
        self._action_in_progress = True

        def task():
            if not self._request_is_current(backend, generation):
                return None
            if muted is None:
                backend.toggle_muted()
            else:
                backend.set_muted(muted)
            return backend.get_state()

        def completed(state):
            if not self._request_is_current(backend, generation):
                return False
            self._action_in_progress = False
            self._poll_generation += 1
            if state is not None:
                self._on_probe_result(state)
            return False

        self._worker.run(
            name="micshield-mute",
            fn=task,
            on_result=completed,
            on_error=lambda _error: completed(
                MicShieldState(False, None, None, error=True)
            ),
        )

    def _ensure_pulse_timer(self) -> None:
        """Run the red-dot ripple only while a mic capture stream is active."""
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
        self.item.icon = self.create_icon(size=self._icon_size)
        if self._notify:
            self._notify()
        return True
