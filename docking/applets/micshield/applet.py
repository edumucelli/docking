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
from docking.applets.micshield.render import render_icon
from docking.applets.micshield.state import (
    DEFAULT_POLL_INTERVAL_S,
    MicShieldState,
    build_tooltip,
    probe_mic_state,
    set_mic_muted,
    stream_label,
    toggle_mic_mute,
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

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._state: MicShieldState = probe_mic_state()
        self._timer_id: int = 0
        self._pulse_timer_id: int = 0
        self._pulse_phase: float = 0.0
        self._worker = BackgroundWorker(logger=log)
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        phase = self._pulse_phase if self._state.active else None
        return render_icon(
            size=size,
            available=self._state.available,
            muted=self._state.muted,
            active=self._state.active,
            pulse_phase=phase,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(self._state)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        GLib.idle_add(self._refresh_once)
        self._timer_id = GLib.timeout_add_seconds(
            DEFAULT_POLL_INTERVAL_S,
            self._tick,
        )
        self._ensure_pulse_timer()

    def stop(self) -> None:
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
            status.append(disabled_menu_item(_("No microphone source found"), gtk=Gtk))
        else:
            status.append(
                disabled_menu_item(
                    _("Microphone muted")
                    if self._state.muted
                    else _("Microphone unmuted"),
                    gtk=Gtk,
                )
            )

            if self._state.active:
                status.append(disabled_menu_item(_("Microphone active"), gtk=Gtk))
                for stream in self._state.streams:
                    status.append(disabled_menu_item(stream_label(stream), gtk=Gtk))
            else:
                status.append(disabled_menu_item(_("Microphone idle"), gtk=Gtk))

        mute_label = (
            _("Unmute Microphone") if self._state.muted else _("Mute Microphone")
        )
        mute = Gtk.MenuItem(label=mute_label)
        mute.set_sensitive(self._state.available)
        mute.connect("activate", lambda _w: self._set_muted(not self._state.muted))

        refresh = Gtk.MenuItem(label=_("Refresh Now"))
        refresh.connect("activate", lambda _w: self._refresh_now())
        return menu_sections(
            status=status,
            primary=[mute],
            refresh=[refresh],
            gtk=Gtk,
        )

    def _refresh_once(self) -> bool:
        self._refresh_now()
        return False

    def _refresh_now(self) -> None:
        self._worker.run(
            name="micshield-poll",
            fn=probe_mic_state,
            on_result=self._on_probe_result,
        )

    def _tick(self) -> bool:
        self._refresh_now()
        return True

    def _on_probe_result(self, state: MicShieldState) -> bool:
        self._state = state
        self._ensure_pulse_timer()
        self.present()
        return False

    def _toggle_mute(self) -> None:
        if not self._state.available:
            return
        self._worker.run(
            name="micshield-toggle",
            fn=toggle_mic_mute,
            on_result=lambda _ok: self._refresh_after_action(),
        )

    def _set_muted(self, muted: bool) -> None:
        if not self._state.available:
            return
        self._worker.run(
            name="micshield-set-mute",
            fn=lambda: set_mic_muted(muted=muted),
            on_result=lambda _ok: self._refresh_after_action(),
        )

    def _refresh_after_action(self) -> bool:
        self._refresh_now()
        return False

    def _ensure_pulse_timer(self) -> None:
        """Run the red-dot ripple only while a mic capture stream is active."""
        should_pulse = self._notify is not None and self._state.active
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
        self._pulse_phase = (
            self._pulse_phase + _PULSE_INTERVAL_MS / _PULSE_PERIOD_MS
        ) % 1.0
        self.item.icon = self.create_icon(size=self._icon_size)
        if self._notify:
            self._notify()
        return True
