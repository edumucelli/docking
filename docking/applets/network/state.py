"""Pure state/parsing helpers for Network applet."""

from __future__ import annotations

from typing import NamedTuple

from docking.i18n import _


class TrafficCounters(NamedTuple):
    """Byte counters for a network interface."""

    rx: int
    tx: int


class TrafficSpeeds(NamedTuple):
    """Download/upload speeds in bytes per second."""

    down: float
    up: float


def parse_proc_net_dev(text: str) -> dict[str, TrafficCounters]:
    """Parse /proc/net/dev into {iface: (rx_bytes, tx_bytes)}."""
    result: dict[str, TrafficCounters] = {}
    for line in text.strip().split("\n")[2:]:
        if ":" not in line:
            continue
        iface, rest = line.split(":", 1)
        fields = rest.split()
        if len(fields) >= 9:
            rx = int(fields[0])
            tx = int(fields[8])
            result[iface.strip()] = TrafficCounters(rx, tx)
    return result


def compute_speeds(
    prev: TrafficCounters,
    curr: TrafficCounters,
    elapsed_s: float,
) -> TrafficSpeeds:
    """Compute (rx_bytes_per_sec, tx_bytes_per_sec) from two samples."""
    if elapsed_s <= 0:
        return TrafficSpeeds(0.0, 0.0)
    rx_delta = max(0, curr.rx - prev.rx)
    tx_delta = max(0, curr.tx - prev.tx)
    return TrafficSpeeds(rx_delta / elapsed_s, tx_delta / elapsed_s)


def format_speed(bps: float) -> str:
    """Format bytes/sec as human-readable string."""
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KB/s"
    if bps < 1024 * 1024 * 1024:
        return f"{bps / (1024 * 1024):.1f} MB/s"
    return f"{bps / (1024 * 1024 * 1024):.1f} GB/s"


def signal_to_icon(strength: int, is_connected: bool, is_wifi: bool) -> str:
    """Map network state to GTK icon name."""
    if not is_connected:
        return "network-offline-symbolic"
    if not is_wifi:
        return "network-wired-symbolic"
    if strength >= 80:
        return "network-wireless-signal-excellent-symbolic"
    if strength >= 60:
        return "network-wireless-signal-good-symbolic"
    if strength >= 40:
        return "network-wireless-signal-ok-symbolic"
    return "network-wireless-signal-weak-symbolic"


def build_tooltip(
    is_connected: bool,
    ssid: str,
    signal_strength: int,
    iface: str,
    ip_address: str,
    rx_speed: float,
    tx_speed: float,
) -> str:
    """Multi-line tooltip with connection details."""
    if not is_connected:
        return _("Network: Not connected")
    lines = []
    if ssid:
        lines.append(f"WiFi: {ssid} ({signal_strength}%)")
    else:
        lines.append(f"Ethernet: {iface}")
    if ip_address:
        lines.append(f"IP: {ip_address}")
    down = format_speed(bps=rx_speed)
    up = format_speed(bps=tx_speed)
    lines.append(f"\u2193 {down}  \u2191 {up}")
    return "\n".join(lines)
