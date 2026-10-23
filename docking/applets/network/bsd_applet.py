"""Native BSD interface status and traffic without NetworkManager dependencies."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections, radio_menu_items
from docking.applets.network import meta
from docking.applets.network.render import create_icon
from docking.applets.network.state import (
    NetworkState,
    TrafficCounters,
    compute_speeds,
    format_speed,
)
from docking.applets.tooltip import structured_tooltip
from docking.i18n import _
from docking.platform import bsd

if TYPE_CHECKING:
    from docking.core.config import Config


class NetworkApplet(Applet):
    id = meta.id
    name = _("Network")
    icon_name = "network-wired-symbolic"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._state = NetworkState()
        self._interfaces: list[bsd.NetworkInterface] = []
        self._previous: TrafficCounters | None = None
        self._previous_iface = ""
        self._previous_time = time.monotonic()
        self._timer_id = 0
        prefs = config.applet_prefs.get(meta.id, {})
        self._interface = str(prefs.get("interface", ""))
        self._speed_overlay = str(prefs.get("speed_overlay", "download"))
        super().__init__(icon_size, config)
        self._tick()

    def create_icon(self, size: int):
        return create_icon(
            size=size,
            is_connected=self._state.is_connected,
            is_wifi=False,
            signal_strength=0,
            rx_speed=self._state.rx_speed,
            tx_speed=self._state.tx_speed,
            speed_overlay=self._speed_overlay,
        )

    def refresh_tooltip(self) -> None:
        state = self._state
        self.item.name = structured_tooltip(
            title=_("Network"),
            primary=state.iface if state.is_connected else _("Not connected"),
            details=[
                state.ip_address,
                (
                    f"\u2193 {format_speed(state.rx_speed)}  "
                    f"\u2191 {format_speed(state.tx_speed)}"
                ),
            ],
        )

    def start(self, notify):
        super().start(notify)
        if not self._timer_id:
            self._timer_id = GLib.timeout_add_seconds(2, self._tick)

    def stop(self):
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def _tick(self) -> bool:
        self._interfaces = bsd.network_interfaces()
        selected = next(
            (i for i in self._interfaces if i.name == self._interface), None
        )
        if selected is None:
            selected = next(iter(self._interfaces), None)
        now = time.monotonic()
        self._state = NetworkState()
        if selected is not None:
            counters = TrafficCounters(selected.received, selected.sent)
            speeds = compute_speeds(
                prev=self._previous
                if self._previous is not None and self._previous_iface == selected.name
                else counters,
                curr=counters,
                elapsed_s=now - self._previous_time,
            )
            self._state = NetworkState(
                is_connected=selected.up and bool(selected.address),
                iface=selected.name,
                ip_address=selected.address,
                rx_speed=speeds.down,
                tx_speed=speeds.up,
            )
            self._previous = counters
            self._previous_iface = selected.name
        else:
            self._previous = None
            self._previous_iface = ""
        self._previous_time = now
        self.present()
        return True

    def _select(self, key: str, value: str):
        if key == "interface":
            self._interface = value
        else:
            self._speed_overlay = value
        self.save_prefs(
            {"interface": self._interface, "speed_overlay": self._speed_overlay}
        )
        self._tick()

    def get_menu_items(self):
        interfaces = radio_menu_items(
            choices=tuple(
                (f"{i.name} {i.address}".strip(), i.name) for i in self._interfaces
            ),
            active_value=self._state.iface,
            on_selected=lambda _widget, value: self._select("interface", value),
            gtk=Gtk,
        )
        display = radio_menu_items(
            choices=(
                (_("Show Download"), "download"),
                (_("Show Upload"), "upload"),
                (_("Hide Speeds"), "none"),
            ),
            active_value=self._speed_overlay,
            on_selected=lambda _widget, value: self._select("speed_overlay", value),
            gtk=Gtk,
        )
        return menu_sections(
            status=[
                disabled_menu_item(
                    self._state.ip_address or _("Not connected"), gtk=Gtk
                )
            ],
            manage=interfaces,
            display=display,
            gtk=Gtk,
        )
