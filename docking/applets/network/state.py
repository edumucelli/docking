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

"""Pure state/parsing helpers for Network applet."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple

from docking.applets.tooltip import structured_tooltip
from docking.applets.units import format_compact_number
from docking.i18n import _
from docking.log import get_logger

log = get_logger("network.state")


class TrafficCounters(NamedTuple):
    """Byte counters for a network interface."""

    rx: int
    tx: int


class TrafficSpeeds(NamedTuple):
    """Download/upload speeds in bytes per second."""

    down: float
    up: float


class AvailableNetwork(NamedTuple):
    """A visible Wi-Fi network candidate for the applet menu."""

    ssid: str
    strength: int
    access_point_path: str
    is_active: bool
    is_connectable: bool = True


@dataclass(frozen=True)
class NetworkState:
    """Visible state of the Network applet.

    Frozen so equality comparison gates re-rendering: the applet only calls
    ``present()`` when this value actually changes between polls.
    """

    is_connected: bool = False
    is_wifi: bool = False
    ssid: str = ""
    signal_strength: int | None = 0
    iface: str = ""
    ip_address: str = ""
    rx_speed: float = 0.0
    tx_speed: float = 0.0
    interface_id: str = ""  # Persistent native identity, when distinct from iface.


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


def format_compact_speed(bps: float) -> str:
    """Format bytes/sec for compact icon labels with explicit byte units."""
    if bps < 1024:
        return f"{bps:.0f}B"
    if bps < 1024 * 1024:
        return f"{format_compact_number(bps / 1024)}KB"
    if bps < 1024 * 1024 * 1024:
        return f"{format_compact_number(bps / (1024 * 1024))}MB"
    return f"{format_compact_number(bps / (1024 * 1024 * 1024))}GB"


def decode_ssid(ssid_bytes: object | None) -> str:
    """Decode an NM SSID byte container into a displayable string."""
    if ssid_bytes is None:
        return ""
    raw = ssid_bytes if isinstance(ssid_bytes, bytes) else ssid_bytes.get_data()
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    return str(raw).strip()


def dedupe_networks(networks: list[AvailableNetwork]) -> list[AvailableNetwork]:
    """Keep one entry per SSID, preferring active and stronger access points."""
    best_by_ssid: dict[str, AvailableNetwork] = {}
    for network in networks:
        if not network.ssid:
            continue
        current = best_by_ssid.get(network.ssid)
        if current is None:
            best_by_ssid[network.ssid] = network
            continue
        if network.is_active and not current.is_active:
            best_by_ssid[network.ssid] = network
            continue
        if network.is_active == current.is_active and (
            network.is_connectable,
            network.strength,
        ) > (current.is_connectable, current.strength):
            best_by_ssid[network.ssid] = network
    return sorted(
        best_by_ssid.values(),
        key=lambda item: (
            not item.is_active,
            -item.strength,
            item.ssid.casefold(),
        ),
    )


def signal_to_icon(strength: int | None, is_connected: bool, is_wifi: bool) -> str:
    """Map network state to GTK icon name."""
    if not is_connected:
        return "network-offline-symbolic"
    if not is_wifi:
        return "network-wired-symbolic"
    if strength is None:
        return "network-wireless-symbolic"
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
    signal_strength: int | None,
    iface: str,
    ip_address: str,
    rx_speed: float,
    tx_speed: float,
    is_wifi: bool = False,
) -> str:
    """Multi-line tooltip with connection details."""
    if not is_connected:
        return structured_tooltip(
            title=_("Network"),
            primary=_("Not connected"),
        )
    primary = (
        f"WiFi: {ssid}"
        + (f" ({signal_strength}%)" if signal_strength is not None else "")
        if ssid
        else f"{_('Network')}: {iface}"
        if is_wifi
        else f"Ethernet: {iface}"
    )
    details = []
    if ip_address:
        details.append(f"IP: {ip_address}")
    down = format_speed(bps=rx_speed)
    up = format_speed(bps=tx_speed)
    details.append(f"\u2193 {down}  \u2191 {up}")
    return structured_tooltip(
        title=_("Network"),
        primary=primary,
        details=details,
    )
