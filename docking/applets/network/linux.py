"""NetworkManager integration for the Network applet backend contract."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("NM", "1.0")
from gi.repository import NM, GLib

from docking.applets.network import meta
from docking.applets.network.backend import (
    ConnectionManager,
    NetworkBackend,
    NetworkSettings,
    VpnConnection,
)
from docking.applets.network.state import (
    AvailableNetwork,
    NetworkState,
    TrafficCounters,
    decode_ssid,
    dedupe_networks,
    parse_proc_net_dev,
)
from docking.log import get_logger, with_context

log = with_context(get_logger(name="network"), applet_id=meta.id)
_PROC_NET_DEV = Path("/proc/net/dev")
WIFI_SCAN_REQUEST_INTERVAL_S = 15.0


def _skip_device_types() -> frozenset[object]:
    """Return device types that should never represent the active network."""
    return frozenset(
        getattr(NM.DeviceType, name)
        for name in ("TUN", "BRIDGE", "LOOPBACK", "DUMMY", "VETH")
        if hasattr(NM.DeviceType, name)
    )


class LinuxNetworkBackend(NetworkBackend, ConnectionManager):
    def __init__(self, interface: str = "") -> None:
        self.interface = interface
        self._nm_client: NM.Client | None = None
        self._nm_handler_id = 0
        self._nm_state_handler_id = 0
        self._last_wifi_scan_request_time = 0.0
        self._changed: Callable[[], None] = lambda: None

    @property
    def settings(self) -> NetworkSettings:
        from docking.applets.network.desktop_settings import create_settings

        return create_settings()

    @property
    def connection_manager(self) -> ConnectionManager | None:
        return self if self._nm_client is not None else None

    def interface_choices(self) -> tuple[tuple[str, str], ...]:
        return ()

    def start(self, changed: Callable[[], None]) -> None:
        self._changed = changed
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
        except GLib.Error:
            log.bind(action="connect_nm").warning("Could not connect to NetworkManager")

    def stop(self) -> None:
        if self._nm_client is not None:
            for handler in (self._nm_handler_id, self._nm_state_handler_id):
                if handler:
                    self._nm_client.disconnect(handler)
        self._nm_handler_id = self._nm_state_handler_id = 0
        self._nm_client = None
        self._changed = lambda: None

    def _on_nm_changed(self, *_args: object) -> None:
        self._changed()

    def state(self) -> NetworkState:
        state = self._compute_nm_state()
        return replace(state, signal_strength=self._refresh_wifi_signal(state=state))

    def counters(self) -> dict[str, TrafficCounters] | None:
        try:
            return parse_proc_net_dev(_PROC_NET_DEV.read_text())
        except OSError as exc:
            log.debug("Failed to read %s: %s", _PROC_NET_DEV, exc)
            return None

    @property
    def networking_enabled(self) -> bool:
        return bool(self._nm_client and self._nm_client.networking_get_enabled())

    @property
    def wireless_enabled(self) -> bool:
        return bool(self._nm_client and self._nm_client.wireless_get_enabled())

    @property
    def wireless_hardware_enabled(self) -> bool:
        return bool(self._nm_client and self._nm_client.wireless_hardware_get_enabled())

    def vpn_connections(self) -> list[VpnConnection]:
        return [
            VpnConnection(
                connection.get_uuid(),
                connection.get_id() or connection.get_uuid(),
                is_active,
            )
            for connection, is_active, _active in self._vpn_connections()
        ]

    def toggle_vpn(self, uuid: str) -> None:
        for connection, is_active, active in self._vpn_connections():
            if connection.get_uuid() == uuid:
                self._toggle_vpn_connection(
                    connection=connection,
                    is_active=is_active,
                    active_connection=active,
                )
                return

    def set_networking_enabled(self, *, enabled: bool) -> None:
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
        self._changed()

    def set_wireless_enabled(self, *, enabled: bool) -> None:
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
        self._changed()

    def available_networks(self) -> list[AvailableNetwork]:
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

    def connect_network(self, *, network: AvailableNetwork) -> None:
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
        )

    def has_wifi_device(self) -> bool:
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

    def _refresh_wifi_signal(self, *, state: NetworkState) -> int | None:
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
                        return int(ap.get_strength())
                    return state.signal_strength
        return state.signal_strength
