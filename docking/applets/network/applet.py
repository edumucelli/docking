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

"""GTK lifecycle glue for Network applet."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("NM", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import NM, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.menu import disabled_menu_item, menu_sections, radio_menu_items
from docking.applets.network import meta
from docking.applets.network.render import create_icon
from docking.applets.network.state import (
    AvailableNetwork,
    NetworkState,
    TrafficCounters,
    build_tooltip,
    compute_speeds,
    connection_info_command,
    decode_ssid,
    dedupe_networks,
    edit_connections_command,
    format_speed,
    open_connection_info,
    open_edit_connections,
    open_hidden_wifi_settings,
    open_new_wifi_settings,
    parse_proc_net_dev,
)
from docking.i18n import _
from docking.log import get_logger, with_context

if TYPE_CHECKING:
    from docking.core.config import Config


def _skip_device_types() -> frozenset[object]:
    """Return device types that should never represent the active network."""
    return frozenset(
        getattr(NM.DeviceType, name)
        for name in ("TUN", "BRIDGE", "LOOPBACK", "DUMMY", "VETH")
        if hasattr(NM.DeviceType, name)
    )


log = with_context(get_logger(name="network"), applet_id=meta.id)
_PROC_NET_DEV = Path("/proc/net/dev")

POLL_INTERVAL_S = 2
WIFI_SCAN_REQUEST_INTERVAL_S = 15.0


class NetworkApplet(Applet):
    """Shows network connection state, wifi signal, and traffic speeds."""

    id = meta.id
    name = _("Network")
    icon_name = "network-wireless-symbolic"

    def __init__(self, icon_size: int, config: Config) -> None:
        self._timer_id: int = 0
        self._nm_client: NM.Client | None = None
        self._nm_handler_id: int = 0
        self._nm_state_handler_id: int = 0

        # Visible state. Frozen so equality gates re-renders.
        self._state = NetworkState()
        # "download", "upload", or "none"
        self._speed_overlay = "download"

        # Traffic tracking
        self._prev_counters: TrafficCounters | None = None
        self._prev_time: float = 0.0
        self._last_wifi_scan_request_time: float = 0.0

        prefs = config.applet_prefs.get(meta.id, {})
        self._speed_overlay = prefs.get("speed_overlay", "download")

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

    def get_menu_items(self) -> list:
        """Show connection info and common network actions."""
        state = self._state
        status: list[Gtk.MenuItem] = []
        if state.ssid:
            status.append(
                disabled_menu_item(
                    _("WiFi: {ssid} ({pct}%)").format(
                        ssid=state.ssid, pct=state.signal_strength
                    ),
                    gtk=Gtk,
                )
            )
        elif state.is_connected:
            status.append(
                disabled_menu_item(
                    _("Ethernet: {iface}").format(iface=state.iface),
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
        info_cmd = connection_info_command()
        if info_cmd is not None:
            info_item = Gtk.MenuItem(label=_("Connection Information"))
            info_item.connect("activate", lambda _widget: open_connection_info())
            settings.append(info_item)

        edit_cmd = edit_connections_command()
        if edit_cmd is not None:
            edit_item = Gtk.MenuItem(label=_("Edit Connections..."))
            edit_item.connect("activate", lambda _widget: open_edit_connections())
            settings.append(edit_item)

        manage: list[Gtk.MenuItem] = []
        if self._nm_client is not None:
            if self._has_wifi_device():
                manage.append(self._build_available_networks_submenu())

                hidden_item = Gtk.MenuItem(
                    label=_("Connect to Hidden Wi-Fi Network...")
                )
                hidden_item.connect(
                    "activate", lambda _widget: open_hidden_wifi_settings()
                )
                manage.append(hidden_item)

                new_item = Gtk.MenuItem(label=_("Create New Wi-Fi Network..."))
                new_item.connect("activate", lambda _widget: open_new_wifi_settings())
                manage.append(new_item)

            networking_item = Gtk.CheckMenuItem(label=_("Enable Networking"))
            networking_item.set_active(self._nm_client.networking_get_enabled())
            networking_item.connect(
                "toggled",
                lambda widget: self._set_networking_enabled(
                    enabled=widget.get_active()
                ),
            )
            manage.append(networking_item)

            if self._has_wifi_device():
                wifi_item = Gtk.CheckMenuItem(label=_("Enable Wi-Fi"))
                wifi_item.set_active(self._nm_client.wireless_get_enabled())
                wifi_item.set_sensitive(self._nm_client.wireless_hardware_get_enabled())
                wifi_item.connect(
                    "toggled",
                    lambda widget: self._set_wireless_enabled(
                        enabled=widget.get_active()
                    ),
                )
                manage.append(wifi_item)

            vpn_item = self._build_vpn_connections_submenu()
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

    def _set_speed_overlay(self, mode: str) -> None:
        self._speed_overlay = mode
        self.save_prefs(prefs={"speed_overlay": mode})
        self.present()

    def _set_networking_enabled(self, *, enabled: bool) -> None:
        if self._nm_client is None:
            return
        try:
            self._nm_client.networking_set_enabled(enabled)
        except GLib.Error as exc:
            log.bind(action="set_networking_enabled").warning(
                "Failed to set networking enabled=%s: %s",
                enabled,
                exc,
            )
            return
        self._refresh_state()

    def _set_wireless_enabled(self, *, enabled: bool) -> None:
        if self._nm_client is None:
            return
        try:
            self._nm_client.wireless_set_enabled(enabled)
        except GLib.Error as exc:
            log.bind(action="set_wireless_enabled").warning(
                "Failed to set Wi-Fi enabled=%s: %s",
                enabled,
                exc,
            )
            return
        self._refresh_state()

    def _build_available_networks_submenu(self) -> Gtk.MenuItem:
        item = Gtk.MenuItem(label=_("Available Networks"))
        submenu = Gtk.Menu()

        for network in self._available_networks():
            label = _("{ssid} ({pct}%)").format(ssid=network.ssid, pct=network.strength)
            if network.is_active:
                label = _("Connected: {label}").format(label=label)
            child = Gtk.MenuItem(label=label)
            child.connect(
                "activate",
                lambda _widget, n=network: self._connect_available_network(network=n),
            )
            submenu.append(child)

        if not submenu.get_children():
            empty = Gtk.MenuItem(label=_("No networks found"))
            empty.set_sensitive(False)
            submenu.append(empty)

        item.set_submenu(submenu)
        return item

    def _build_vpn_connections_submenu(self) -> Gtk.MenuItem | None:
        vpn_connections = self._vpn_connections()
        if not vpn_connections:
            return None

        item = Gtk.MenuItem(label=_("VPN Connections"))
        submenu = Gtk.Menu()
        for connection, is_active, active_connection in vpn_connections:
            label = connection.get_id() or connection.get_uuid() or _("Unnamed VPN")
            child = Gtk.MenuItem(label=label)
            child.connect(
                "activate",
                lambda _widget, c=connection, active=is_active, ac=active_connection: (
                    self._toggle_vpn_connection(
                        connection=c,
                        is_active=active,
                        active_connection=ac,
                    )
                ),
            )
            submenu.append(child)
        item.set_submenu(submenu)
        return item

    def _available_networks(self) -> list[AvailableNetwork]:
        if self._nm_client is None:
            return []

        networks: list[AvailableNetwork] = []
        for device in self._nm_client.get_devices():
            if device.get_device_type() != NM.DeviceType.WIFI:
                continue
            self._request_wifi_scan(device=device)
            active_ap = device.get_active_access_point()
            active_path = active_ap.get_path() if active_ap is not None else ""
            for ap in device.get_access_points():
                ssid = decode_ssid(ap.get_ssid())
                if not ssid:
                    continue
                networks.append(
                    AvailableNetwork(
                        ssid=ssid,
                        strength=ap.get_strength(),
                        access_point_path=ap.get_path(),
                        is_active=ap.get_path() == active_path,
                    )
                )
        return dedupe_networks(networks=networks)

    def _request_wifi_scan(self, *, device: NM.Device) -> None:
        now = time.monotonic()
        if now - self._last_wifi_scan_request_time < WIFI_SCAN_REQUEST_INTERVAL_S:
            return
        try:
            requested = device.request_scan(None)
        except GLib.Error as exc:
            log.bind(action="request_wifi_scan").debug(
                "Failed to request Wi-Fi scan on %s: %s",
                device.get_iface(),
                exc,
            )
            return
        if requested:
            self._last_wifi_scan_request_time = now

    def _connect_available_network(self, *, network: AvailableNetwork) -> None:
        if self._nm_client is None:
            return
        match = self._find_wifi_access_point(network=network)
        if match is None:
            return
        device, access_point = match
        connection = self._find_saved_wifi_connection(ssid=network.ssid)
        try:
            if connection is not None:
                self._nm_client.activate_connection_async(
                    connection,
                    device,
                    access_point.get_path(),
                    None,
                    self._on_activate_connection_finished,
                    None,
                )
            else:
                self._nm_client.add_and_activate_connection_async(
                    None,
                    device,
                    access_point.get_path(),
                    None,
                    self._on_add_and_activate_connection_finished,
                    None,
                )
        except GLib.Error as exc:
            log.bind(action="connect_available_network", ssid=network.ssid).warning(
                "Failed to connect to Wi-Fi network %s: %s",
                network.ssid,
                exc,
            )

    def _toggle_vpn_connection(
        self, *, connection, is_active: bool, active_connection
    ) -> None:
        if self._nm_client is None:
            return
        try:
            if is_active and active_connection is not None:
                self._nm_client.deactivate_connection_async(
                    active_connection,
                    None,
                    self._on_deactivate_connection_finished,
                    None,
                )
                return
            self._nm_client.activate_connection_async(
                connection,
                None,
                None,
                None,
                self._on_activate_connection_finished,
                None,
            )
        except GLib.Error as exc:
            log.bind(action="toggle_vpn_connection").warning(
                "Failed to toggle VPN connection %s: %s",
                connection.get_id() or connection.get_uuid() or "<unknown>",
                exc,
            )

    def _find_wifi_access_point(
        self, *, network: AvailableNetwork
    ) -> tuple[NM.Device, NM.AccessPoint] | None:
        if self._nm_client is None:
            return None
        for device in self._nm_client.get_devices():
            if device.get_device_type() != NM.DeviceType.WIFI:
                continue
            for ap in device.get_access_points():
                if ap.get_path() == network.access_point_path:
                    return device, ap
        return None

    def _find_saved_wifi_connection(self, *, ssid: str) -> object | None:
        if self._nm_client is None:
            return None
        for connection in self._nm_client.get_connections():
            setting = connection.get_setting_wireless()
            if setting is None:
                continue
            if decode_ssid(setting.get_ssid()) == ssid:
                return connection
        return None

    def _vpn_connections(self) -> list[tuple[object, bool, object | None]]:
        if self._nm_client is None:
            return []

        active_by_uuid = {
            active.get_uuid(): active
            for active in self._nm_client.get_active_connections()
            if active.get_vpn()
        }
        result = []
        for connection in self._nm_client.get_connections():
            if connection.get_setting_vpn() is None:
                continue
            uuid = connection.get_uuid()
            active_connection = active_by_uuid.get(uuid)
            result.append(
                (
                    connection,
                    active_connection is not None,
                    active_connection,
                )
            )
        result.sort(key=lambda item: (not item[1], (item[0].get_id() or "").casefold()))
        return result

    def _on_activate_connection_finished(self, client, result, _user_data) -> None:
        try:
            client.activate_connection_finish(result)
        except GLib.Error as exc:
            log.bind(action="activate_connection_finish").warning(
                "Failed to activate saved Wi-Fi connection: %s",
                exc,
            )

    def _on_add_and_activate_connection_finished(
        self, client, result, _user_data
    ) -> None:
        try:
            client.add_and_activate_connection_finish(result)
        except GLib.Error as exc:
            log.bind(action="add_and_activate_connection_finish").warning(
                "Failed to add and activate Wi-Fi connection: %s",
                exc,
            )

    def _on_deactivate_connection_finished(self, client, result, _user_data) -> None:
        try:
            client.deactivate_connection_finish(result)
        except GLib.Error as exc:
            log.bind(action="deactivate_connection_finish").warning(
                "Failed to deactivate VPN connection: %s",
                exc,
            )

    def start(self, notify: Callable[[], None]) -> None:
        """Connect to NetworkManager and start traffic polling."""
        super().start(notify=notify)
        try:
            self._nm_client = NM.Client.new(None)
            self._nm_handler_id = self._nm_client.connect(
                "notify::active-connections",
                self._on_nm_changed,
            )
            self._nm_state_handler_id = self._nm_client.connect(
                "notify::state",
                self._on_nm_changed,
            )
            self._refresh_state()
        except GLib.Error:
            log.bind(action="connect_nm").warning(
                "Could not connect to NetworkManager",
            )
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        """Disconnect NM signals and stop timer."""
        if self._nm_client:
            if self._nm_handler_id:
                self._nm_client.disconnect(self._nm_handler_id)
                self._nm_handler_id = 0
            if self._nm_state_handler_id:
                self._nm_client.disconnect(self._nm_state_handler_id)
                self._nm_state_handler_id = 0
        self._nm_client = None
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def _on_nm_changed(self, *_args: object) -> None:
        """NM active-connections changed: refresh and re-render on change."""
        self._refresh_state()

    def _refresh_state(self) -> None:
        """Recompute the full visible state and present only on change."""
        new_state = self._compute_nm_state()
        new_state = self._apply_traffic(state=new_state)
        if new_state != self._state:
            self._state = new_state
            self.present()

    def _compute_nm_state(self) -> NetworkState:
        """Return a NetworkState reflecting current NetworkManager status."""
        if not self._nm_client:
            return NetworkState()

        # Collect candidates, prioritize wifi > ethernet > other
        best_device: NM.Device | None = None
        best_priority = -1

        for conn in self._nm_client.get_active_connections():
            if conn.get_state() != NM.ActiveConnectionState.ACTIVATED:
                continue
            devices = conn.get_devices()
            if not devices:
                continue
            for device in devices:
                priority = self._device_priority(device=device)
                if priority < 0:
                    continue
                if priority > best_priority:
                    best_priority = priority
                    best_device = device

        if not best_device:
            return NetworkState()

        iface = best_device.get_iface() or ""
        ip_address = ""
        ip4_config = best_device.get_ip4_config()
        if ip4_config:
            addrs = ip4_config.get_addresses()
            if addrs:
                ip_address = addrs[0].get_address() or ""

        is_wifi = False
        ssid = ""
        signal_strength = 0
        if isinstance(best_device, NM.DeviceWifi):
            is_wifi = True
            ap = best_device.get_active_access_point()
            if ap:
                ssid_bytes = ap.get_ssid()
                if ssid_bytes:
                    ssid = ssid_bytes.get_data().decode("utf-8", errors="replace")
                signal_strength = ap.get_strength()

        return NetworkState(
            is_connected=True,
            is_wifi=is_wifi,
            ssid=ssid,
            signal_strength=signal_strength,
            iface=iface,
            ip_address=ip_address,
            # Traffic speeds get filled in by _apply_traffic; keep previous values
            # so equality comparison doesn't flap between NM refresh and tick.
            rx_speed=self._state.rx_speed,
            tx_speed=self._state.tx_speed,
        )

    def _tick(self) -> bool:
        """Poll traffic counters and wifi signal, re-render only on change."""
        self._refresh_state()
        return True

    def _apply_traffic(self, *, state: NetworkState) -> NetworkState:
        """Return ``state`` with rx/tx speeds updated from /proc/net/dev."""
        if not state.iface:
            self._prev_counters = None
            return replace(state, rx_speed=0.0, tx_speed=0.0)
        try:
            with _PROC_NET_DEV.open() as f:
                counters = parse_proc_net_dev(text=f.read())
        except OSError as exc:
            log.debug("Failed to read %s: %s", _PROC_NET_DEV, exc)
            return state

        now = time.monotonic()
        current = counters.get(state.iface)
        rx_speed = state.rx_speed
        tx_speed = state.tx_speed
        if current and self._prev_counters:
            elapsed = now - self._prev_time
            rx_speed, tx_speed = compute_speeds(
                prev=self._prev_counters,
                curr=current,
                elapsed_s=elapsed,
            )
        if current:
            self._prev_counters = current
        self._prev_time = now
        # NM may have stale signal strength; refresh while we have the client.
        signal = self._refresh_wifi_signal(state=state)
        return NetworkState(
            is_connected=state.is_connected,
            is_wifi=state.is_wifi,
            ssid=state.ssid,
            signal_strength=signal,
            iface=state.iface,
            ip_address=state.ip_address,
            rx_speed=rx_speed,
            tx_speed=tx_speed,
        )

    def _refresh_wifi_signal(self, *, state: NetworkState) -> int:
        """Return the current wifi signal strength, or ``state.signal_strength``."""
        if not self._nm_client or not state.is_wifi:
            return state.signal_strength
        for conn in self._nm_client.get_active_connections():
            if conn.get_state() != NM.ActiveConnectionState.ACTIVATED:
                continue
            for device in conn.get_devices() or ():
                if isinstance(device, NM.DeviceWifi):
                    ap = device.get_active_access_point()
                    if ap:
                        return ap.get_strength()
                    return state.signal_strength
        return state.signal_strength

    def _build_tooltip(self) -> str:
        return build_tooltip(
            is_connected=self._state.is_connected,
            ssid=self._state.ssid,
            signal_strength=self._state.signal_strength,
            iface=self._state.iface,
            ip_address=self._state.ip_address,
            rx_speed=self._state.rx_speed,
            tx_speed=self._state.tx_speed,
        )

    def _has_wifi_device(self) -> bool:
        if self._nm_client is None:
            return False
        for device in self._nm_client.get_devices():
            if device.get_device_type() == NM.DeviceType.WIFI:
                return True
        return False

    @staticmethod
    def _device_priority(device: NM.Device) -> int:
        """Rank devices so the applet prefers real uplinks over virtual links."""
        dev_type = device.get_device_type()

        # Skip virtual/internal links that should not represent the current network.
        if dev_type in _skip_device_types():
            return -1

        if dev_type == NM.DeviceType.WIFI:
            return 2
        if dev_type == NM.DeviceType.ETHERNET:
            return 1
        return 0
