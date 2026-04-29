"""GTK lifecycle glue for desk-presence applet."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.deskpresence import meta
from docking.applets.deskpresence.idle import get_idle_ms
from docking.applets.deskpresence.render import render_icon
from docking.applets.deskpresence.state import (
    DEFAULT_POLL_INTERVAL_S,
    MAX_IDLE_THRESHOLD_S,
    MIN_IDLE_THRESHOLD_S,
    Presence,
    apply_tick,
    build_tooltip,
    prefs_from_mapping,
    prefs_payload,
    state_from_prefs,
)
from docking.applets.menu import radio_submenu
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.applets.deskpresence.state import PresenceState
    from docking.core.config import Config

log = with_context(get_logger(name="deskpresence"), applet_id=meta.id)

_IDLE_THRESHOLD_PRESETS_S: tuple[float, ...] = (60.0, 120.0, 300.0, 600.0)

# Pulse animation cadence when AT_DESK.
_PULSE_INTERVAL_MS = 60  # ~17 fps -- smooth enough without taxing CPU
_PULSE_PERIOD_MS = 1800  # one full ripple cycle


class DeskpresenceApplet(Applet):
    """Tracks at-desk vs away time from X11 idle signals."""

    id = meta.id
    name = _("Desk Presence")
    icon_name = "user-available"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._pulse_timer_id: int = 0
        self._pulse_phase: float = 0.0
        self._idle_probe: Callable[[], int | None] = get_idle_ms

        prefs = prefs_from_mapping(
            config.applet_prefs.get(meta.id, {}) if config else None
        )
        self._state: PresenceState = state_from_prefs(prefs=prefs)

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        phase = self._pulse_phase if self._state.presence is Presence.AT_DESK else None
        return render_icon(
            size=size,
            presence=self._state.presence,
            at_desk_seconds=self._state.at_desk_seconds,
            pulse_phase=phase,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = build_tooltip(state=self._state, now_epoch=time.time())

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        header = Gtk.MenuItem(
            label=_("{status}").format(
                status=_presence_text(self._state.presence),
            )
        )
        header.set_sensitive(False)
        items.append(header)
        items.append(Gtk.SeparatorMenuItem())

        items.append(
            radio_submenu(
                label=_("Idle Threshold"),
                choices=tuple(
                    (_threshold_label(preset_s), preset_s)
                    for preset_s in _IDLE_THRESHOLD_PRESETS_S
                ),
                active_value=self._state.idle_threshold_s,
                is_active=lambda value: abs(self._state.idle_threshold_s - value) < 0.5,
                on_selected=lambda _widget, value: self._set_threshold(seconds=value),
                gtk=Gtk,
            )
        )

        reset = Gtk.MenuItem(label=_("Reset Today"))
        reset.connect("activate", lambda _w: self._reset_today())
        items.append(reset)

        return items

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        # First tick is nearly immediate so the dot color reflects reality;
        # subsequent ticks run on the regular cadence.
        self._timer_id = GLib.timeout_add_seconds(
            int(DEFAULT_POLL_INTERVAL_S), self._tick
        )
        GLib.idle_add(self._tick_once)
        self._ensure_pulse_timer()

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
        super().stop()

    def _tick_once(self) -> bool:
        self._tick()
        return False

    def _tick(self) -> bool:
        idle_ms = self._idle_probe()
        apply_tick(
            state=self._state,
            idle_ms=idle_ms,
            now_epoch=time.time(),
            today=datetime.now(timezone.utc).date(),
        )
        self._save_prefs()
        self._ensure_pulse_timer()
        self.present()
        return True

    def _ensure_pulse_timer(self) -> None:
        """Run the pulse animation only while the user is at the desk."""
        should_pulse = self._state.presence is Presence.AT_DESK
        if should_pulse and not self._pulse_timer_id:
            self._pulse_timer_id = GLib.timeout_add(
                _PULSE_INTERVAL_MS, self._pulse_tick
            )
        elif not should_pulse and self._pulse_timer_id:
            GLib.source_remove(self._pulse_timer_id)
            self._pulse_timer_id = 0
            self._pulse_phase = 0.0

    def _pulse_tick(self) -> bool:
        self._pulse_phase = (
            self._pulse_phase + _PULSE_INTERVAL_MS / _PULSE_PERIOD_MS
        ) % 1.0
        # Repaint the icon only; skip tooltip / prefs churn.
        self.item.icon = self.create_icon(size=self._icon_size)
        if self._notify:
            self._notify()
        return True

    def _set_threshold(self, *, seconds: float) -> None:
        clamped = max(MIN_IDLE_THRESHOLD_S, min(MAX_IDLE_THRESHOLD_S, seconds))
        self._state.idle_threshold_s = clamped
        self._save_prefs()
        self.present()

    def _reset_today(self) -> None:
        self._state.at_desk_seconds = 0.0
        self._state.away_seconds = 0.0
        self._state.session_start_epoch = time.time()
        self._save_prefs()
        self.present()

    def _save_prefs(self) -> None:
        self.save_prefs(prefs=prefs_payload(state=self._state))


def _presence_text(presence: Presence) -> str:
    return {
        Presence.AT_DESK: _("At desk"),
        Presence.AWAY: _("Away"),
        Presence.UNKNOWN: _("Status unknown"),
    }[presence]


def _threshold_label(seconds: float) -> str:
    minutes = int(seconds) // 60
    if minutes >= 1:
        return _("{m} min").format(m=minutes)
    return _("{s} sec").format(s=int(seconds))
