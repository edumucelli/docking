"""Network applet -- shows connection state, signal strength, and traffic speeds.

Uses NetworkManager (via PyGObject NM 1.0) for connection state and wifi info.
Reads /proc/net/dev every 2 seconds for upload/download traffic counters.
Icon from GTK theme matching wifi signal strength or wired/offline state.
Speed overlay rendered via Cairo at bottom center.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Callable

import cairo

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("NM", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, NM, Pango, PangoCairo  # noqa: E402

from docking.applets.base import Applet, load_theme_icon
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger("network")

POLL_INTERVAL_S = 2


# -- Pure functions (testable without GTK) ------------------------------------


def parse_proc_net_dev(text: str) -> dict[str, tuple[int, int]]:
    """Parse /proc/net/dev into {iface: (rx_bytes, tx_bytes)}.

    Skips the two header lines. Each data line:
      iface: rx_bytes rx_packets ... (8 fields) tx_bytes tx_packets ... (8 fields)
    """
    result: dict[str, tuple[int, int]] = {}
    for line in text.strip().split("\n")[2:]:
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        fields = rest.split()
        if len(fields) >= 9:
            rx = int(fields[0])
            tx = int(fields[8])
            result[iface.strip()] = (rx, tx)
    return result


def compute_speeds(
    prev: tuple[int, int], curr: tuple[int, int], elapsed_s: float
) -> tuple[float, float]:
    """Compute (rx_bytes_per_sec, tx_bytes_per_sec) from two samples."""
    if elapsed_s <= 0:
        return 0.0, 0.0
    rx_delta = max(0, curr[0] - prev[0])
    tx_delta = max(0, curr[1] - prev[1])
    return rx_delta / elapsed_s, tx_delta / elapsed_s


def format_speed(bps: float) -> str:
    """Format bytes/sec as human-readable string."""
    if bps < 1024:
        return f"{bps:.0f} B/s"
    elif bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    elif bps < 1024 * 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    else:
        return f"{bps / (1024 * 1024 * 1024):.1f} GB/s"


def signal_to_icon(strength: int, is_connected: bool, is_wifi: bool) -> str:
    """Map network state to GTK icon name."""
    if not is_connected:
        return "network-offline-symbolic"
    if not is_wifi:
        return "network-wired-symbolic"
    if strength >= 80:
        return "network-wireless-signal-excellent-symbolic"
    elif strength >= 60:
        return "network-wireless-signal-good-symbolic"
    elif strength >= 40:
        return "network-wireless-signal-ok-symbolic"
    else:
        return "network-wireless-signal-weak-symbolic"


# -- Applet -------------------------------------------------------------------


class NetworkApplet(Applet):
    """Shows network connection state, wifi signal, and traffic speeds."""

    id = "network"
    name = "Network"
    icon_name = "network-wireless-symbolic"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer_id: int = 0
        self._nm_client: NM.Client | None = None
        self._nm_handler_id: int = 0

        # State
        self._is_connected = False
        self._is_wifi = False
        self._ssid = ""
        self._signal_strength = 0
        self._iface = ""
        self._ip_address = ""
        self._rx_speed = 0.0
        self._tx_speed = 0.0

        # Traffic tracking
        self._prev_counters: tuple[int, int] | None = None
        self._prev_time: float = 0.0

        super().__init__(icon_size, config)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Load network icon with speed overlay."""
        icon_name = signal_to_icon(
            self._signal_strength, self._is_connected, self._is_wifi
        )
        base = load_theme_icon(icon_name, size)

        if hasattr(self, "item"):
            self.item.name = self._build_tooltip()

        if not base or not self._is_connected:
            return base

        # Overlay speed text
        rx_str = format_speed(self._rx_speed)
        tx_str = format_speed(self._tx_speed)
        if self._tx_speed > 1024:
            overlay = f"\u2193{rx_str.split()[0]} \u2191{tx_str.split()[0]}"
        else:
            overlay = f"\u2193{rx_str.split()[0]}"

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(cr, base, 0, 0)
        cr.paint()

        font_size = max(1, int(size * 0.18))
        layout = PangoCairo.create_layout(cr)
        layout.set_font_description(Pango.FontDescription(f"Sans Bold {font_size}px"))
        layout.set_text(overlay, -1)

        _ink, logical = layout.get_pixel_extents()
        tx = (size - logical.width) / 2 - logical.x
        ty = size - logical.height - max(1, size * 0.02) - logical.y

        cr.move_to(tx, ty)
        PangoCairo.layout_path(cr, layout)
        cr.set_source_rgba(0, 0, 0, 0.8)
        cr.set_line_width(max(1.5, size * 0.04))
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke_preserve()
        cr.set_source_rgba(1, 1, 1, 1)
        cr.fill()

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def get_menu_items(self) -> list:
        """Show connection info."""
        from gi.repository import Gtk

        items = []
        if self._ssid:
            header = Gtk.MenuItem(
                label=f"WiFi: {self._ssid} ({self._signal_strength}%)"
            )
            header.set_sensitive(False)
            items.append(header)
        elif self._is_connected:
            header = Gtk.MenuItem(label=f"Ethernet: {self._iface}")
            header.set_sensitive(False)
            items.append(header)
        else:
            header = Gtk.MenuItem(label="Not connected")
            header.set_sensitive(False)
            items.append(header)

        if self._ip_address:
            ip_item = Gtk.MenuItem(label=f"IP: {self._ip_address}")
            ip_item.set_sensitive(False)
            items.append(ip_item)

        if self._is_connected:
            speed_item = Gtk.MenuItem(
                label=f"\u2193 {format_speed(self._rx_speed)}  \u2191 {format_speed(self._tx_speed)}"
            )
            speed_item.set_sensitive(False)
            items.append(speed_item)

        return items

    def start(self, notify: Callable[[], None]) -> None:
        """Connect to NetworkManager and start traffic polling."""
        super().start(notify)
        try:
            self._nm_client = NM.Client.new(None)
            self._nm_handler_id = self._nm_client.connect(
                "notify::active-connections", self._on_nm_changed
            )
            self._update_nm_state()
        except GLib.Error:
            _log.warning("Could not connect to NetworkManager")
        self._timer_id = GLib.timeout_add_seconds(POLL_INTERVAL_S, self._tick)

    def stop(self) -> None:
        """Disconnect NM signals and stop timer."""
        if self._nm_client and self._nm_handler_id:
            self._nm_client.disconnect(self._nm_handler_id)
            self._nm_handler_id = 0
        self._nm_client = None
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    def _on_nm_changed(self, *_args: object) -> None:
        """NM active-connections changed: update state immediately."""
        self._update_nm_state()
        self.refresh_icon()

    def _update_nm_state(self) -> None:
        """Read current connection info from NetworkManager.

        Iterates all active connections and picks the most relevant one:
        wifi > ethernet > other. Skips tun/bridge/loopback devices
        (VPN tunnels, Docker bridges) which would hide the real connection.
        """
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
            device = devices[0]
            dev_type = device.get_device_type()

            # Skip tun, bridge, loopback (VPN, Docker, lo)
            if dev_type in (
                NM.DeviceType.TUN,
                NM.DeviceType.BRIDGE,
            ):
                continue

            # Priority: wifi=2, ethernet=1, other=0
            if dev_type == NM.DeviceType.WIFI:
                priority = 2
            elif dev_type == NM.DeviceType.ETHERNET:
                priority = 1
            else:
                priority = 0

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
        self.refresh_icon()
        return True

    def _update_traffic(self) -> None:
        """Read /proc/net/dev and compute speeds for active interface."""
        if not self._iface:
            self._rx_speed = 0.0
            self._tx_speed = 0.0
            return
        try:
            with open("/proc/net/dev") as f:
                counters = parse_proc_net_dev(f.read())
        except OSError:
            return

        now = time.monotonic()
        current = counters.get(self._iface)
        if current and self._prev_counters:
            elapsed = now - self._prev_time
            self._rx_speed, self._tx_speed = compute_speeds(
                self._prev_counters, current, elapsed
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
            devices = conn.get_devices()
            if devices and isinstance(devices[0], NM.DeviceWifi):
                ap = devices[0].get_active_access_point()
                if ap:
                    self._signal_strength = ap.get_strength()
            break

    def _build_tooltip(self) -> str:
        """Multi-line tooltip with connection details."""
        if not self._is_connected:
            return "Network: Not connected"
        lines = []
        if self._ssid:
            lines.append(f"WiFi: {self._ssid} ({self._signal_strength}%)")
        else:
            lines.append(f"Ethernet: {self._iface}")
        if self._ip_address:
            lines.append(f"IP: {self._ip_address}")
        lines.append(
            f"\u2193 {format_speed(self._rx_speed)}  \u2191 {format_speed(self._tx_speed)}"
        )
        return "\n".join(lines)
