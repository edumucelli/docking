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

"""Shared rendering, menus, traffic calculation, and lifecycle for Network."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections, radio_menu_items
from docking.applets.network import meta
from docking.applets.network.backend import ConnectionManager, create_backend
from docking.applets.network.render import create_icon
from docking.applets.network.state import (
    NetworkState,
    TrafficCounters,
    build_tooltip,
    compute_speeds,
    format_speed,
)
from docking.i18n import _

if TYPE_CHECKING:
    from docking.core.config import Config

POLL_INTERVAL_S = 2


class NetworkApplet(Applet):
    """Show connection status and traffic through the selected backend."""

    id = meta.id
    name = _("Network")
    icon_name = "network-wireless-symbolic"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._timer_id = 0
        self._state = NetworkState()
        self._prev_counters: TrafficCounters | None = None
        self._prev_time = 0.0
        self._prev_iface = ""
        prefs = config.applet_prefs.get(meta.id, {})
        self._speed_overlay = prefs.get("speed_overlay", "download")
        self._backend = create_backend(str(prefs.get("interface", "")))
        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int):
        """Load network icon with optional speed overlay."""
        return create_icon(
            size=size,
            is_connected=self._state.is_connected,
            is_wifi=self._state.is_wifi,
            signal_strength=self._state.signal_strength,
            rx_speed=self._state.rx_speed,
            tx_speed=self._state.tx_speed,
            speed_overlay=self._speed_overlay,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = self._build_tooltip()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Show connection info and common network actions."""
        state = self._state
        status: list[Gtk.MenuItem] = []
        if state.ssid and state.signal_strength is not None:
            status.append(
                disabled_menu_item(
                    _("WiFi: {ssid} ({pct}%)").format(
                        ssid=state.ssid, pct=state.signal_strength
                    ),
                    gtk=Gtk,
                )
            )
        elif state.ssid:
            status.append(disabled_menu_item(f"{_('Network')}: {state.ssid}", gtk=Gtk))
        elif state.is_connected:
            status.append(
                disabled_menu_item(
                    f"{_('Network')}: {state.iface}"
                    if state.is_wifi
                    else _("Ethernet: {iface}").format(iface=state.iface),
                    gtk=Gtk,
                )
            )
        else:
            status.append(disabled_menu_item(_("Not connected"), gtk=Gtk))

        if state.ip_address:
            status.append(
                disabled_menu_item(_("IP: {ip}").format(ip=state.ip_address), gtk=Gtk)
            )

        if state.is_connected:
            down = format_speed(bps=state.rx_speed)
            up = format_speed(bps=state.tx_speed)
            status.append(disabled_menu_item(f"\u2193 {down}  \u2191 {up}", gtk=Gtk))

        settings: list[Gtk.MenuItem] = []
        platform_settings = self._backend.settings
        if platform_settings.connection_info_available():
            info_item = Gtk.MenuItem(label=_("Connection Information"))
            info_item.connect(
                "activate", lambda _widget: platform_settings.open_connection_info()
            )
            settings.append(info_item)

        if platform_settings.edit_connections_available():
            edit_item = Gtk.MenuItem(label=_("Edit Connections..."))
            edit_item.connect(
                "activate", lambda _widget: platform_settings.open_edit_connections()
            )
            settings.append(edit_item)

        manage: list[Gtk.MenuItem] = []
        choices = self._backend.interface_choices()
        active_interface = self._backend.interface
        if active_interface and state.interface_id:
            active_interface = state.interface_id
        elif not active_interface and not any(key == "" for _label, key in choices):
            active_interface = state.iface
        manage.extend(
            radio_menu_items(
                choices=choices,
                active_value=active_interface,
                on_selected=lambda _widget, value: self._set_interface(value),
                gtk=Gtk,
            )
        )
        manager = self._backend.connection_manager
        if manager is not None:
            if manager.has_wifi_device():
                manage.append(self._build_available_networks_submenu(manager))
                hidden_item = Gtk.MenuItem(
                    label=_("Connect to Hidden Wi-Fi Network...")
                )
                hidden_item.connect(
                    "activate",
                    lambda _widget: platform_settings.open_hidden_wifi_settings(),
                )
                manage.append(hidden_item)
                new_item = Gtk.MenuItem(label=_("Create New Wi-Fi Network..."))
                new_item.connect(
                    "activate",
                    lambda _widget: platform_settings.open_new_wifi_settings(),
                )
                manage.append(new_item)

            if manager.capabilities.networking_toggle:
                networking_item = Gtk.CheckMenuItem(label=_("Enable Networking"))
                networking_item.set_active(manager.networking_enabled)
                networking_item.connect(
                    "toggled",
                    lambda widget: manager.set_networking_enabled(
                        enabled=widget.get_active()
                    ),
                )
                manage.append(networking_item)
            if manager.has_wifi_device() and manager.capabilities.wireless_toggle:
                wifi_item = Gtk.CheckMenuItem(label=_("Enable Wi-Fi"))
                wifi_item.set_active(manager.wireless_enabled)
                wifi_item.set_sensitive(manager.wireless_hardware_enabled)
                wifi_item.connect(
                    "toggled",
                    lambda widget: manager.set_wireless_enabled(
                        enabled=widget.get_active()
                    ),
                )
                manage.append(wifi_item)
            vpn_item = self._build_vpn_connections_submenu(manager)
            if vpn_item is not None:
                manage.append(vpn_item)

        display = radio_menu_items(
            choices=(
                (_("Show Download"), "download"),
                (_("Show Upload"), "upload"),
                (_("Hide Speeds"), "none"),
            ),
            active_value=self._speed_overlay,
            on_selected=lambda _widget, value: self._set_speed_overlay(mode=value),
            gtk=Gtk,
        )

        return menu_sections(
            status=status,
            display=display,
            manage=manage,
            settings=settings,
            gtk=Gtk,
        )

    def _save_preferences(self) -> None:
        self.save_prefs(
            prefs={
                "interface": self._backend.interface,
                "speed_overlay": self._speed_overlay,
            }
        )

    def _set_speed_overlay(self, mode: str) -> None:
        self._speed_overlay = mode
        self._save_preferences()
        self.present()

    def _set_interface(self, interface: str) -> None:
        self._backend.interface = interface
        self._save_preferences()
        self._refresh_state()

    def _build_available_networks_submenu(
        self, manager: ConnectionManager
    ) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=_("Available Networks"))
        submenu = Gtk.Menu()

        for network in manager.available_networks():
            label = _("{ssid} ({pct}%)").format(ssid=network.ssid, pct=network.strength)
            if network.is_active:
                label = _("Connected: {label}").format(label=label)
            child = Gtk.MenuItem(label=label)
            child.set_sensitive(network.is_connectable)
            child.connect(
                "activate",
                lambda _widget, n=network: manager.connect_network(network=n),
            )
            submenu.append(child)

        if not submenu.get_children():
            empty = Gtk.MenuItem(
                label=manager.networks_unavailable_reason or _("No networks found")
            )
            empty.set_sensitive(False)
            submenu.append(empty)

        item.set_submenu(submenu)
        return item

    def _build_vpn_connections_submenu(
        self, manager: ConnectionManager
    ) -> Gtk.MenuItem | None:
        connections = manager.vpn_connections()
        if not connections:
            return None
        item = Gtk.MenuItem(label=_("VPN Connections"))
        submenu = Gtk.Menu()
        for connection in connections:
            child = Gtk.CheckMenuItem(label=connection.label or _("Unnamed VPN"))
            child.set_active(connection.is_active)
            child.set_sensitive(not connection.is_pending and connection.can_toggle)
            if connection.description:
                child.set_tooltip_text(connection.description)
            child.connect(
                "activate",
                lambda _widget, uuid=connection.uuid: manager.toggle_vpn(uuid),
            )
            submenu.append(child)
        item.set_submenu(submenu)
        return item

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify=notify)
        self._backend.start(self._refresh_state)
        self._refresh_state()
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        self._backend.stop()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def _refresh_state(self) -> None:
        state = self._backend.state()
        if (state.interface_id or state.iface) == (
            self._state.interface_id or self._state.iface
        ):
            state = replace(
                state, rx_speed=self._state.rx_speed, tx_speed=self._state.tx_speed
            )
        state = self._apply_traffic(state=state)
        if state != self._state:
            self._state = state
            self.present()

    def _tick(self) -> bool:
        self._refresh_state()
        return True

    def _apply_traffic(self, *, state: NetworkState) -> NetworkState:
        if not state.iface:
            self._prev_counters = None
            self._prev_iface = ""
            return replace(state, rx_speed=0.0, tx_speed=0.0)
        counters = self._backend.counters()
        if counters is None:
            return state
        now = time.monotonic()
        current = counters.get(state.iface)
        rx_speed, tx_speed = 0.0, 0.0
        if (
            current is not None
            and self._prev_counters is not None
            and self._prev_iface == (state.interface_id or state.iface)
        ):
            rx_speed, tx_speed = compute_speeds(
                self._prev_counters, current, now - self._prev_time
            )
        self._prev_counters = current
        self._prev_iface = state.interface_id or state.iface
        self._prev_time = now
        return replace(state, rx_speed=rx_speed, tx_speed=tx_speed)

    def _build_tooltip(self) -> str:
        return build_tooltip(
            is_connected=self._state.is_connected,
            ssid=self._state.ssid,
            signal_strength=self._state.signal_strength,
            iface=self._state.iface,
            ip_address=self._state.ip_address,
            rx_speed=self._state.rx_speed,
            tx_speed=self._state.tx_speed,
            is_wifi=self._state.is_wifi,
        )
