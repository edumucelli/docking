"""GTK lifecycle glue for Network applet."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("NM", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import NM, GLib, Gtk

from docking.applets.base import Applet
from docking.applets.network import meta
from docking.applets.network.render import create_icon
from docking.applets.network.state import (
    TrafficCounters,
    build_tooltip,
    compute_speeds,
    format_speed,
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


_log = with_context(get_logger(name="network"), applet_id=meta.id)
_PROC_NET_DEV = Path("/proc/net/dev")

POLL_INTERVAL_S = 2


class NetworkApplet(Applet):
    """Shows network connection state, wifi signal, and traffic speeds."""

    id = meta.id
    name = _("Network")
    icon_name = "network-wireless-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._nm_client: NM.Client | None = None
        self._nm_handler_id: int = 0
        self._nm_state_handler_id: int = 0

        # State
        self._is_connected = False
        self._is_wifi = False
        self._ssid = ""
        self._signal_strength = 0
        self._iface = ""
        self._ip_address = ""
        self._rx_speed = 0.0
        self._tx_speed = 0.0
        # "download", "upload", or "none"
        self._speed_overlay = "download"

        # Traffic tracking
        self._prev_counters: TrafficCounters | None = None
        self._prev_time: float = 0.0

        if config:
            prefs = config.applet_prefs.get(meta.id, {})
            self._speed_overlay = prefs.get("speed_overlay", "download")

        super().__init__(icon_size=icon_size, config=config)
        self.present()

    def create_icon(self, size: int):
        """Load network icon with optional speed overlay."""
        return create_icon(
            size=size,
            is_connected=self._is_connected,
            is_wifi=self._is_wifi,
            signal_strength=self._signal_strength,
            rx_speed=self._rx_speed,
            tx_speed=self._tx_speed,
            speed_overlay=self._speed_overlay,
        )

    def refresh_tooltip(self) -> None:
        self.item.name = self._build_tooltip()

    def get_menu_items(self) -> list:
        """Show connection info."""
        items = []
        if self._ssid:
            header = Gtk.MenuItem(
                label=_("WiFi: {ssid} ({pct}%)").format(
                    ssid=self._ssid, pct=self._signal_strength
                )
            )
            header.set_sensitive(False)
            items.append(header)
        elif self._is_connected:
            header = Gtk.MenuItem(
                label=_("Ethernet: {iface}").format(iface=self._iface)
            )
            header.set_sensitive(False)
            items.append(header)
        else:
            header = Gtk.MenuItem(label=_("Not connected"))
            header.set_sensitive(False)
            items.append(header)

        if self._ip_address:
            ip_item = Gtk.MenuItem(label=_("IP: {ip}").format(ip=self._ip_address))
            ip_item.set_sensitive(False)
            items.append(ip_item)

        if self._is_connected:
            down = format_speed(bps=self._rx_speed)
            up = format_speed(bps=self._tx_speed)
            speed_item = Gtk.MenuItem(label=f"\u2193 {down}  \u2191 {up}")
            speed_item.set_sensitive(False)
            items.append(speed_item)

        for label, mode in [
            ("Show Download", "download"),
            ("Show Upload", "upload"),
            ("Hide Speeds", "none"),
        ]:
            mi = Gtk.CheckMenuItem(label=label)
            mi.set_active(self._speed_overlay == mode)
            mi.connect(
                "toggled",
                lambda _w, m=mode: self._set_speed_overlay(mode=m),
            )
            items.append(mi)

        return items

    def _set_speed_overlay(self, mode: str) -> None:
        self._speed_overlay = mode
        self.save_prefs(prefs={"speed_overlay": mode})
        self.present()

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
            self._update_nm_state()
        except GLib.Error:
            _log.bind(action="connect_nm").warning(
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
        """NM active-connections changed: update state immediately."""
        self._update_nm_state()
        self.present()

    def _update_nm_state(self) -> None:
        """Read current connection info from NetworkManager."""
        if not self._nm_client:
            return

        self._is_connected = False
        self._is_wifi = False
        self._ssid = ""
        self._signal_strength = 0
        self._iface = ""
        self._ip_address = ""

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
            return

        self._is_connected = True
        self._iface = best_device.get_iface() or ""

        # IP address
        ip4_config = best_device.get_ip4_config()
        if ip4_config:
            addrs = ip4_config.get_addresses()
            if addrs:
                self._ip_address = addrs[0].get_address() or ""

        # WiFi specifics
        if isinstance(best_device, NM.DeviceWifi):
            self._is_wifi = True
            ap = best_device.get_active_access_point()
            if ap:
                ssid_bytes = ap.get_ssid()
                if ssid_bytes:
                    self._ssid = ssid_bytes.get_data().decode("utf-8", errors="replace")
                self._signal_strength = ap.get_strength()

    def _tick(self) -> bool:
        """Poll traffic counters and wifi signal."""
        self._update_traffic()
        self._update_wifi_signal()
        self.present()
        return True

    def _update_traffic(self) -> None:
        """Read /proc/net/dev and compute speeds for active interface."""
        if not self._iface:
            self._rx_speed = 0.0
            self._tx_speed = 0.0
            return
        try:
            with _PROC_NET_DEV.open() as f:
                counters = parse_proc_net_dev(text=f.read())
        except OSError:
            return

        now = time.monotonic()
        current = counters.get(self._iface)
        if current and self._prev_counters:
            elapsed = now - self._prev_time
            self._rx_speed, self._tx_speed = compute_speeds(
                prev=self._prev_counters,
                curr=current,
                elapsed_s=elapsed,
            )
        if current:
            self._prev_counters = current
        self._prev_time = now

    def _update_wifi_signal(self) -> None:
        """Re-read wifi signal from NM (access point strength can change)."""
        if not self._nm_client or not self._is_wifi:
            return
        for conn in self._nm_client.get_active_connections():
            if conn.get_state() != NM.ActiveConnectionState.ACTIVATED:
                continue
            for device in conn.get_devices() or ():
                if isinstance(device, NM.DeviceWifi):
                    ap = device.get_active_access_point()
                    if ap:
                        self._signal_strength = ap.get_strength()
                    return

    def _build_tooltip(self) -> str:
        return build_tooltip(
            is_connected=self._is_connected,
            ssid=self._ssid,
            signal_strength=self._signal_strength,
            iface=self._iface,
            ip_address=self._ip_address,
            rx_speed=self._rx_speed,
            tx_speed=self._tx_speed,
        )

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
