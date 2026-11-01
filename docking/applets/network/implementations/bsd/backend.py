"""BSD integration for the Network applet backend contract."""

import importlib
import socket
from collections.abc import Callable
from typing import Any, NamedTuple

from docking.applets.network.backend import (
    ConnectionManager,
    NetworkBackend,
    NetworkSettings,
)
from docking.applets.network.state import NetworkState, TrafficCounters
from docking.log import get_logger


class BsdNetworkBackend(NetworkBackend):
    """Read interface status and counters without GTK or NetworkManager."""

    def __init__(self, interface: str = "") -> None:
        self.interface = interface
        self.interfaces: list[NetworkInterface] = []

    @property
    def settings(self) -> NetworkSettings:
        from docking.applets.network.implementations.unix.desktop_settings import (
            create_settings,
        )

        return create_settings()

    @property
    def connection_manager(self) -> ConnectionManager | None:
        return None

    def start(self, changed: Callable[[], None]) -> None:
        """Status is polled; no persistent subscriptions are required."""

    def stop(self) -> None:
        """No subscriptions or handles to release."""

    def interface_choices(self) -> tuple[tuple[str, str], ...]:
        return tuple((f"{i.name} {i.address}".strip(), i.name) for i in self.interfaces)

    def state(self) -> NetworkState:
        self.interfaces = network_interfaces()
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


log = get_logger("network.bsd")


def _psutil() -> Any:
    return importlib.import_module("psutil")


class NetworkInterface(NamedTuple):
    name: str
    address: str
    up: bool
    received: int
    sent: int


def network_interfaces() -> list[NetworkInterface]:
    try:
        util = _psutil()
        counters = util.net_io_counters(pernic=True)
        addresses = util.net_if_addrs()
        result = []
        for name, status in util.net_if_stats().items():
            if str(name).startswith("lo") or name not in counters:
                continue
            address = next(
                (
                    str(a.address)
                    for family in (socket.AF_INET, socket.AF_INET6)
                    for a in addresses.get(name, ())
                    if a.family == family
                ),
                "",
            )
            counter = counters[name]
            result.append(
                NetworkInterface(
                    str(name),
                    address,
                    bool(status.isup),
                    int(counter.bytes_recv),
                    int(counter.bytes_sent),
                )
            )
        return sorted(
            result,
            key=lambda interface: (
                not interface.up,
                not interface.address,
                interface.name,
            ),
        )
    except Exception as exc:
        log.debug("Network interfaces unavailable: %s", exc)
        return []
