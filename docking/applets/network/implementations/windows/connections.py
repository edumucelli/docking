"""Windows implementation of shared network connection-management actions."""

from __future__ import annotations

import time
from collections.abc import Callable

from docking.applets.network.backend import (
    ConnectionManager,
    NetworkCapabilities,
    VpnConnection,
)
from docking.applets.network.implementations.windows.vpn import WindowsVpn
from docking.applets.network.implementations.windows.wifi import (
    _Candidate,
    _RadioState,
    _Wlan,
    _wlan,
)
from docking.applets.network.state import AvailableNetwork, dedupe_networks
from docking.i18n import _
from docking.log import get_logger

log = get_logger("network.windows_connections")
_SCAN_INTERVAL_S = 15.0


class WindowsConnectionManager(ConnectionManager):
    def __init__(self, *, api: _Wlan | None = None, vpn: WindowsVpn | None = None):
        self._api = api
        self._vpn = vpn if vpn is not None else WindowsVpn()
        self._identifiers: list[str] = []
        self._radios: list[_RadioState] = []
        self._choices: dict[str, _Candidate] = {}
        self._last_scan = float("-inf")
        self._reason = ""
        self.changed: Callable[[], None] = lambda: None

    def _native(self) -> _Wlan:
        return self._api if self._api is not None else _wlan()

    def refresh(self) -> None:
        try:
            self._identifiers, self._radios = self._native().radios()
        except OSError as exc:
            log.debug("WLAN service unavailable: %s", exc)
            self._identifiers, self._radios, self._choices = [], [], {}

    @property
    def capabilities(self) -> NetworkCapabilities:
        # Windows has no nonprivileged NetworkManager-style global network switch.
        return NetworkCapabilities(
            networking_toggle=False, wireless_toggle=bool(self._radios)
        )

    @property
    def networks_unavailable_reason(self) -> str:
        return self._reason

    @property
    def networking_enabled(self) -> bool:
        raise NotImplementedError("Global network switch is not supported")

    @property
    def wireless_enabled(self) -> bool:
        return any(r.software for r in self._radios)

    @property
    def wireless_hardware_enabled(self) -> bool:
        return any(r.hardware for r in self._radios)

    def has_wifi_device(self) -> bool:
        return bool(self._identifiers)

    def available_networks(self) -> list[AvailableNetwork]:
        now = time.monotonic()
        try:
            scan = now - self._last_scan >= _SCAN_INTERVAL_S
            if scan:
                self._last_scan = now
            values = self._native().available(scan=scan)
        except OSError as exc:
            log.debug("Wi-Fi discovery unavailable: %s", exc)
            self._reason = (
                _("Permission denied") if exc.errno == 5 else _("Unavailable")
            )
            self._choices = {}
            return []
        self._reason = ""
        self._choices = {v.key: v for v in values}
        return dedupe_networks([v.visible() for v in values])

    def connect_network(self, *, network: AvailableNetwork) -> None:
        choice = self._choices.get(network.access_point_path)
        if choice is None or choice.visible().ssid != network.ssid:
            return
        try:
            # Revalidate exact adapter/raw SSID/profile before an old menu acts.
            fresh = next(
                (
                    c
                    for c in self._native().available(scan=False)
                    if c.key == choice.key
                ),
                None,
            )
            if fresh is None or not fresh.connectable:
                return
            if fresh.profile:
                self._native().connect(fresh)
            else:
                from docking.platform.native.windows.shell import open_default_target

                open_default_target("ms-settings:network-wifi")
        except OSError as exc:
            log.warning("Could not connect Wi-Fi profile: %s", exc)
        self.changed()

    def set_networking_enabled(self, *, enabled: bool) -> None:
        raise NotImplementedError("Global network switch is not supported")

    def set_wireless_enabled(self, *, enabled: bool) -> None:
        try:
            self._native().set_radio(enabled=enabled)
        except OSError as exc:
            log.warning("Could not change Wi-Fi radio: %s", exc)
        self.refresh()
        self.changed()

    def vpn_connections(self) -> list[VpnConnection]:
        self._vpn.changed = self.changed
        return self._vpn.connections()

    def toggle_vpn(self, uuid: str) -> None:
        self._vpn.changed = self.changed
        self._vpn.toggle(uuid)

    def stop(self) -> None:
        self._vpn.stop()
        self._identifiers, self._radios, self._choices = [], [], {}
        self._reason = ""
        self._last_scan = float("-inf")
        self.changed = lambda: None
