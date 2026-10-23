"""Common network backend contract and environment-based selection."""

from collections.abc import Callable
from typing import NamedTuple, Protocol

from docking.applets.network.state import (
    AvailableNetwork,
    NetworkState,
    TrafficCounters,
)
from docking.platform import environment


class VpnConnection(NamedTuple):
    uuid: str
    label: str
    is_active: bool


class ConnectionManager(Protocol):
    """Optional connection-management capability, independent of rendering."""

    @property
    def networking_enabled(self) -> bool: ...
    @property
    def wireless_enabled(self) -> bool: ...
    @property
    def wireless_hardware_enabled(self) -> bool: ...
    def has_wifi_device(self) -> bool: ...
    def available_networks(self) -> list[AvailableNetwork]: ...
    def connect_network(self, *, network: AvailableNetwork) -> None: ...
    def set_networking_enabled(self, *, enabled: bool) -> None: ...
    def set_wireless_enabled(self, *, enabled: bool) -> None: ...
    def vpn_connections(self) -> list[VpnConnection]: ...
    def toggle_vpn(self, uuid: str) -> None: ...


class NetworkBackend(Protocol):
    interface: str

    @property
    def connection_manager(self) -> ConnectionManager | None: ...
    def start(self, changed: Callable[[], None]) -> None: ...
    def stop(self) -> None: ...
    def state(self) -> NetworkState: ...
    def counters(self) -> dict[str, TrafficCounters] | None: ...
    def interface_choices(self) -> tuple[tuple[str, str], ...]: ...


def create_backend(interface: str = "") -> NetworkBackend:
    if environment.is_bsd():
        from docking.applets.network.bsd import BsdNetworkBackend

        return BsdNetworkBackend(interface)
    from docking.applets.network.networkmanager import NetworkManagerBackend

    return NetworkManagerBackend(interface)
