"""Common network backend contract and environment-based selection."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import NamedTuple

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


class ConnectionManager(ABC):
    """Optional connection-management capability, independent of rendering."""

    @property
    @abstractmethod
    def networking_enabled(self) -> bool: ...
    @property
    @abstractmethod
    def wireless_enabled(self) -> bool: ...
    @property
    @abstractmethod
    def wireless_hardware_enabled(self) -> bool: ...
    @abstractmethod
    def has_wifi_device(self) -> bool: ...
    @abstractmethod
    def available_networks(self) -> list[AvailableNetwork]: ...
    @abstractmethod
    def connect_network(self, *, network: AvailableNetwork) -> None: ...
    @abstractmethod
    def set_networking_enabled(self, *, enabled: bool) -> None: ...
    @abstractmethod
    def set_wireless_enabled(self, *, enabled: bool) -> None: ...
    @abstractmethod
    def vpn_connections(self) -> list[VpnConnection]: ...
    @abstractmethod
    def toggle_vpn(self, uuid: str) -> None: ...


class NetworkBackend(ABC):
    interface: str

    @property
    @abstractmethod
    def connection_manager(self) -> ConnectionManager | None: ...
    @abstractmethod
    def start(self, changed: Callable[[], None]) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...
    @abstractmethod
    def state(self) -> NetworkState: ...
    @abstractmethod
    def counters(self) -> dict[str, TrafficCounters] | None: ...
    @abstractmethod
    def interface_choices(self) -> tuple[tuple[str, str], ...]: ...


def create_backend(interface: str = "") -> NetworkBackend:
    if environment.is_bsd():
        from docking.applets.network.bsd import BsdNetworkBackend

        return BsdNetworkBackend(interface)
    from docking.applets.network.linux import LinuxNetworkBackend

    return LinuxNetworkBackend(interface)
