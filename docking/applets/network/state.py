"""Pure state/parsing helpers for Network applet."""

from __future__ import annotations

import shutil
import subprocess
from typing import NamedTuple

from docking.applets.tooltip import structured_tooltip
from docking.applets.units import format_compact_number
from docking.i18n import _
from docking.log import get_logger
from docking.platform.environment import flatpak

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


_CONNECTION_INFO_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("gnome-control-center", "wifi"),
    ("gnome-control-center", "network"),
    ("mate-network-properties",),
    ("kcmshell6", "kcm_networkmanagement"),
    ("kcmshell5", "kcm_networkmanagement"),
    ("systemsettings", "kcm_networkmanagement"),
)

_EDIT_CONNECTIONS_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("nm-connection-editor",),
    ("gnome-control-center", "network"),
    ("mate-network-properties",),
    ("kcmshell6", "kcm_networkmanagement"),
    ("kcmshell5", "kcm_networkmanagement"),
    ("systemsettings", "kcm_networkmanagement"),
)


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


def connection_info_command() -> list[str] | None:
    """Return the first available desktop network-info/settings command."""
    return _available_desktop_command(_CONNECTION_INFO_COMMANDS)


def edit_connections_command() -> list[str] | None:
    """Return the first available network-connections editor command."""
    return _available_desktop_command(_EDIT_CONNECTIONS_COMMANDS)


def open_connection_info() -> bool:
    """Launch the desktop network information/settings tool when available."""
    return _open_command(cmd=connection_info_command(), action="open_connection_info")


def open_edit_connections() -> bool:
    """Launch the desktop network-connections editor when available."""
    return _open_command(
        cmd=edit_connections_command(),
        action="open_edit_connections",
    )


def open_hidden_wifi_settings() -> bool:
    """Launch the desktop network editor for hidden Wi-Fi setup."""
    return _open_command(
        cmd=edit_connections_command() or connection_info_command(),
        action="open_hidden_wifi_settings",
    )


def open_new_wifi_settings() -> bool:
    """Launch the desktop network editor for creating a new Wi-Fi network."""
    return _open_command(
        cmd=edit_connections_command() or connection_info_command(),
        action="open_new_wifi_settings",
    )


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
        if (
            network.is_active == current.is_active
            and network.strength > current.strength
        ):
            best_by_ssid[network.ssid] = network
    return sorted(
        best_by_ssid.values(),
        key=lambda item: (
            not item.is_active,
            -item.strength,
            item.ssid.casefold(),
        ),
    )


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
        return structured_tooltip(
            title=_("Network"),
            primary=_("Not connected"),
        )
    primary = f"WiFi: {ssid} ({signal_strength}%)" if ssid else f"Ethernet: {iface}"
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


def _open_command(*, cmd: list[str] | None, action: str) -> bool:
    if cmd is None:
        return False
    try:
        subprocess.Popen(cmd, start_new_session=True)
    except OSError as exc:
        log.bind(action=action).warning("Failed to run %s: %s", cmd, exc)
        return False
    return True


def _available_desktop_command(
    candidates: tuple[tuple[str, ...], ...],
) -> list[str] | None:
    flatpak_spawn = flatpak.spawn_path()
    for cmd in candidates:
        if flatpak_spawn is not None:
            host_command = flatpak.host_command(list(cmd))
            if flatpak.host_command_available(cmd[0]) and host_command is not None:
                return host_command
            continue
        if shutil.which(cmd[0]):
            return list(cmd)
    return None
