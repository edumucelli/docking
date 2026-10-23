"""Native BSD network samples for the shared Network applet."""

from docking.applets.network.state import NetworkState, TrafficCounters
from docking.platform import bsd


class NativeNetworkBackend:
    """Read interface status and counters without GTK or NetworkManager."""

    def __init__(self, interface: str = "") -> None:
        self.interface = interface
        self.interfaces: list[bsd.NetworkInterface] = []

    def state(self) -> NetworkState:
        self.interfaces = bsd.network_interfaces()
        selected = next((i for i in self.interfaces if i.name == self.interface), None)
        if selected is None:
            selected = next(iter(self.interfaces), None)
        if selected is None:
            return NetworkState()
        return NetworkState(
            is_connected=selected.up and bool(selected.address),
            iface=selected.name,
            ip_address=selected.address,
        )

    def counters(self) -> dict[str, TrafficCounters]:
        return {i.name: TrafficCounters(i.received, i.sent) for i in self.interfaces}
