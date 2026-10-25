"""Windows implementation of the NetworkBackend contract, using IP Helper."""

from __future__ import annotations

import ctypes
import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING, ClassVar

from docking.applets.network.backend import (
    ConnectionManager,
    NetworkBackend,
    NetworkSettings,
)
from docking.applets.network.state import NetworkState, TrafficCounters
from docking.i18n import _
from docking.log import get_logger
from docking.platform.backends.windows.ffi import bind as _bind

if TYPE_CHECKING:
    from docking.applets.network.windows_wifi import WindowsWifiManager

log = get_logger("network.windows")

AF_INET = 2
AF_INET6 = 23
IF_TYPE_LOOPBACK = 24
IF_TYPE_WIFI = 71


@dataclass(frozen=True)
class _Adapter:
    identifier: str
    name: str
    index: int
    kind: int
    up: bool
    addresses: tuple[str, ...]
    ipv4_metric: int = 0
    ipv6_metric: int = 0
    counters: TrafficCounters | None = None


@dataclass(frozen=True)
class _DefaultRoute:
    index: int
    family: int
    metric: int


class SocketAddress(ctypes.Structure):
    _fields_ = [("address", ctypes.c_void_p), ("length", ctypes.c_int32)]


class UnicastAddress(ctypes.Structure):
    pass


UnicastAddress._fields_ = [
    ("alignment", ctypes.c_uint64),
    ("next", ctypes.POINTER(UnicastAddress)),
    ("socket", SocketAddress),
    ("prefix_origin", ctypes.c_uint32),
    ("suffix_origin", ctypes.c_uint32),
    ("dad_state", ctypes.c_uint32),
    ("valid_lifetime", ctypes.c_uint32),
    ("preferred_lifetime", ctypes.c_uint32),
    ("lease_lifetime", ctypes.c_uint32),
    ("prefix_length", ctypes.c_ubyte),
]


class AdapterAddress(ctypes.Structure):
    """IP_ADAPTER_ADDRESSES_LH prefix through its interface LUID."""


AdapterAddress._fields_ = [
    ("length", ctypes.c_uint32),
    ("index", ctypes.c_uint32),
    ("next", ctypes.POINTER(AdapterAddress)),
    ("identifier", ctypes.c_char_p),
    ("unicast", ctypes.POINTER(UnicastAddress)),
    ("anycast", ctypes.c_void_p),
    ("multicast", ctypes.c_void_p),
    ("dns_server", ctypes.c_void_p),
    ("dns_suffix", ctypes.c_wchar_p),
    ("description", ctypes.c_wchar_p),
    ("name", ctypes.c_wchar_p),
    ("physical_address", ctypes.c_ubyte * 8),
    ("physical_address_length", ctypes.c_uint32),
    ("flags", ctypes.c_uint32),
    ("mtu", ctypes.c_uint32),
    ("kind", ctypes.c_uint32),
    ("oper_status", ctypes.c_uint32),
    ("ipv6_index", ctypes.c_uint32),
    ("zone_indices", ctypes.c_uint32 * 16),
    ("prefix", ctypes.c_void_p),
    ("transmit_link_speed", ctypes.c_uint64),
    ("receive_link_speed", ctypes.c_uint64),
    ("wins_server", ctypes.c_void_p),
    ("gateway", ctypes.c_void_p),
    ("ipv4_metric", ctypes.c_uint32),
    ("ipv6_metric", ctypes.c_uint32),
    ("luid", ctypes.c_uint64),
]


class InterfaceRow(ctypes.Structure):
    """Complete MIB_IF_ROW2; Windows writes the whole caller-owned structure."""

    _fields_ = [
        ("luid", ctypes.c_uint64),
        ("index", ctypes.c_uint32),
        ("guid", ctypes.c_ubyte * 16),
        ("alias", ctypes.c_uint16 * 257),
        ("description", ctypes.c_uint16 * 257),
        ("physical_address_length", ctypes.c_uint32),
        ("physical_address", ctypes.c_ubyte * 32),
        ("permanent_address", ctypes.c_ubyte * 32),
        *[
            (name, ctypes.c_uint32)
            for name in (
                "mtu",
                "kind",
                "tunnel_type",
                "media_type",
                "physical_medium_type",
                "access_type",
                "direction_type",
            )
        ],
        ("status_flags", ctypes.c_ubyte),
        ("oper_status", ctypes.c_uint32),
        ("admin_status", ctypes.c_uint32),
        ("media_connect_state", ctypes.c_uint32),
        ("network_guid", ctypes.c_ubyte * 16),
        ("connection_type", ctypes.c_uint32),
        *[
            (name, ctypes.c_uint64)
            for name in (
                "transmit_link_speed",
                "receive_link_speed",
                "in_octets",
                "in_ucast_pkts",
                "in_nucast_pkts",
                "in_discards",
                "in_errors",
                "in_unknown_protos",
                "in_ucast_octets",
                "in_multicast_octets",
                "in_broadcast_octets",
                "out_octets",
                "out_ucast_pkts",
                "out_nucast_pkts",
                "out_discards",
                "out_errors",
                "out_ucast_octets",
                "out_multicast_octets",
                "out_broadcast_octets",
                "out_qlen",
            )
        ],
    ]


class SockaddrInet(ctypes.Union):
    _fields_: ClassVar[list[tuple[str, object]]] = [
        ("family", ctypes.c_uint16),
        ("alignment", ctypes.c_uint32),
        ("data", ctypes.c_ubyte * 28),
    ]


class AddressPrefix(ctypes.Structure):
    _fields_ = [("address", SockaddrInet), ("length", ctypes.c_ubyte)]


class RouteRow(ctypes.Structure):
    _fields_ = [
        ("luid", ctypes.c_uint64),
        ("index", ctypes.c_uint32),
        ("destination", AddressPrefix),
        ("next_hop", SockaddrInet),
        ("site_prefix_length", ctypes.c_ubyte),
        ("valid_lifetime", ctypes.c_uint32),
        ("preferred_lifetime", ctypes.c_uint32),
        ("metric", ctypes.c_uint32),
        ("protocol", ctypes.c_uint32),
        ("loopback", ctypes.c_ubyte),
        ("autoconfigure", ctypes.c_ubyte),
        ("publish", ctypes.c_ubyte),
        ("immortal", ctypes.c_ubyte),
        ("age", ctypes.c_uint32),
        ("origin", ctypes.c_uint32),
    ]


class RouteTable(ctypes.Structure):
    _fields_ = [("count", ctypes.c_uint32), ("rows", RouteRow * 1)]


def socket_address(value: SocketAddress) -> str | None:
    if not value.address or not 8 <= value.length <= 128:
        return None
    raw = ctypes.string_at(value.address, value.length)
    family = int.from_bytes(raw[:2], "little")
    if family == AF_INET and len(raw) >= 16:
        address = ipaddress.IPv4Address(raw[4:8])
    elif family == AF_INET6 and len(raw) >= 28:
        address = ipaddress.IPv6Address(raw[8:24])
        scope = int.from_bytes(raw[24:28], "little")
        if scope and address.is_link_local:
            return f"{address}%{scope}"
    else:
        return None
    return None if address.is_loopback or address.is_unspecified else str(address)


class _IPHelper:
    def __init__(self):
        dll = ctypes.WinDLL("iphlpapi", use_last_error=True)
        self._adapters = _bind(
            dll,
            "GetAdaptersAddresses",
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        )
        self._interface = _bind(
            dll, "GetIfEntry2", ctypes.c_uint32, ctypes.POINTER(InterfaceRow)
        )
        self._routes = _bind(
            dll,
            "GetIpForwardTable2",
            ctypes.c_uint32,
            ctypes.c_uint16,
            ctypes.POINTER(ctypes.c_void_p),
        )
        self._free = _bind(dll, "FreeMibTable", None, ctypes.c_void_p)

    def _counters(self, luid: int) -> TrafficCounters | None:
        if not luid:
            return None
        # A removed adapter's numeric index can be reused between API calls.
        value = InterfaceRow(luid=luid)
        result = self._interface(ctypes.byref(value))
        if result:
            log.debug("GetIfEntry2(LUID=%s) failed: %s", luid, result)
            return None
        return TrafficCounters(int(value.in_octets), int(value.out_octets))

    def adapters(self) -> list[_Adapter]:
        size = ctypes.c_uint32(15 * 1024)
        for _attempt in range(3):
            if not 0 < size.value <= 16 * 1024 * 1024:
                raise OSError("Invalid adapter buffer size")
            buffer = ctypes.create_string_buffer(size.value)
            # Keep unicast and friendly names; omit DNS/anycast/multicast work.
            result = self._adapters(0, 2 | 4 | 8, None, buffer, ctypes.byref(size))
            if result != 111:  # ERROR_BUFFER_OVERFLOW after a topology change
                break
        if result == 232:  # ERROR_NO_DATA
            return []
        if result:
            raise OSError(result, "GetAdaptersAddresses failed")
        values: list[_Adapter] = []
        seen: set[int] = set()
        node = ctypes.cast(buffer, ctypes.POINTER(AdapterAddress))
        while node:
            address = ctypes.addressof(node.contents)
            if address in seen or len(seen) >= 4096:
                raise OSError("Invalid adapter chain")
            seen.add(address)
            value = node.contents
            if value.length < AdapterAddress.luid.offset + 8:
                raise OSError("Unsupported adapter structure version")
            node = value.next
            if value.kind == IF_TYPE_LOOPBACK or not value.identifier:
                continue
            identifier = value.identifier.decode("ascii", errors="replace").lower()
            addresses: list[str] = []
            seen_unicast: set[int] = set()
            unicast = value.unicast
            while unicast:
                pointer = ctypes.addressof(unicast.contents)
                if pointer in seen_unicast or len(seen_unicast) >= 4096:
                    raise OSError("Invalid unicast chain")
                seen_unicast.add(pointer)
                entry = unicast.contents
                if entry.dad_state in (3, 4):  # deprecated (still valid), preferred
                    ip = socket_address(entry.socket)
                    if ip is not None:
                        addresses.append(ip)
                unicast = entry.next
            index = int(value.index or value.ipv6_index)
            values.append(
                _Adapter(
                    identifier,
                    value.name or identifier,
                    index,
                    int(value.kind),
                    value.oper_status == 1,
                    tuple(dict.fromkeys(addresses)),
                    int(value.ipv4_metric),
                    int(value.ipv6_metric),
                    self._counters(int(value.luid)),
                )
            )
        return values

    def routes(self) -> list[_DefaultRoute]:
        pointer = ctypes.c_void_p()
        result = self._routes(0, ctypes.byref(pointer))
        if result == 1168:  # ERROR_NOT_FOUND
            return []
        if result:
            raise OSError(result, "GetIpForwardTable2 failed")
        try:
            if not pointer.value:
                raise OSError("Missing route table")
            count = int(ctypes.cast(pointer, ctypes.POINTER(RouteTable)).contents.count)
            if count > 65536:
                raise OSError("Invalid route table count")
            rows = (RouteRow * count).from_address(
                pointer.value + RouteTable.rows.offset
            )
            routes = []
            for row in rows:
                family = int(row.destination.address.family)
                raw = bytes(row.destination.address.data)
                address = raw[4:8] if family == AF_INET else raw[8:24]
                if (
                    family in (AF_INET, AF_INET6)
                    and row.destination.length == 0
                    and not any(address)
                    and row.valid_lifetime
                    and not row.loopback
                    and row.metric != 0xFFFFFFFF
                ):
                    routes.append(
                        _DefaultRoute(int(row.index), family, int(row.metric))
                    )
            return routes
        finally:
            if pointer.value:
                self._free(pointer)


@lru_cache(maxsize=1)
def _ip_helper() -> _IPHelper:
    return _IPHelper()


def _address(adapter: _Adapter) -> str:
    return next(
        iter(
            sorted(
                adapter.addresses,
                key=lambda value: (
                    ipaddress.ip_address(value.split("%")[0]).is_link_local,
                    ipaddress.ip_address(value.split("%")[0]).version,
                ),
            )
        ),
        "",
    )


class WindowsNetworkBackend(NetworkBackend):
    def __init__(
        self,
        interface: str = "",
        *,
        api: _IPHelper | None = None,
        wifi: WindowsWifiManager | None = None,
    ):
        self.interface = interface
        self._api = api
        self._adapters: list[_Adapter] = []
        self._counters: dict[str, TrafficCounters] | None = None
        self._wifi = wifi
        self._changed: Callable[[], None] = lambda: None

    @property
    def settings(self) -> NetworkSettings:
        from docking.platform.backends.windows.shell import open_default_target

        return NetworkSettings(
            lambda: True,
            lambda: True,
            lambda: open_default_target("ms-settings:network-status"),
            lambda: open_default_target("ms-settings:network-advancedsettings"),
            lambda: open_default_target("ms-settings:network-wifisettings"),
            lambda: open_default_target("ms-settings:network-mobilehotspot"),
        )

    @property
    def connection_manager(self) -> ConnectionManager:
        if self._wifi is None:
            from docking.applets.network.windows_wifi import WindowsWifiManager

            self._wifi = WindowsWifiManager()
        self._wifi.changed = self._changed
        self._wifi.refresh()
        return self._wifi

    def start(self, changed: Callable[[], None]) -> None:
        """The applet's two-second timer samples native state and counters."""
        self._changed = changed

    def stop(self) -> None:
        self._adapters = []
        self._counters = None
        self._changed = lambda: None
        if self._wifi is not None:
            self._wifi.stop()

    def interface_choices(self) -> tuple[tuple[str, str], ...]:
        return (
            (_("Auto"), ""),
            *(
                (f"{a.name} {_address(a)}".strip(), a.identifier)
                for a in self._adapters
            ),
        )

    def state(self) -> NetworkState:
        api = self._api if self._api is not None else _ip_helper()
        try:
            self._adapters = api.adapters()
        except OSError as exc:
            log.debug("Native adapters unavailable: %s", exc)
            self._adapters, self._counters = [], None
            return NetworkState()
        self._counters = {
            a.name: a.counters for a in self._adapters if a.counters is not None
        }
        if self.interface:
            # Never silently switch a requested (possibly unplugged) adapter.
            selected = next(
                (
                    a
                    for a in self._adapters
                    if self.interface.casefold()
                    in (a.identifier.casefold(), a.name.casefold())
                ),
                None,
            )
        else:
            connected = [a for a in self._adapters if a.up and _address(a)]
            physical = [a for a in connected if a.kind not in (23, 131)]
            connected = physical or connected
            try:
                routes = api.routes()
            except OSError as exc:
                log.debug("Native routes unavailable: %s", exc)
                routes = []
            costs = {
                a.identifier: min(
                    (
                        (
                            0 if r.family == AF_INET else 1,
                            r.metric
                            + (a.ipv4_metric if r.family == AF_INET else a.ipv6_metric),
                        )
                        for r in routes
                        if r.index == a.index
                    ),
                    default=(2, 0),
                )
                for a in connected
            }
            selected = min(
                connected,
                key=lambda a: (*costs[a.identifier], a.name.casefold()),
                default=None,
            )
        if selected is None:
            return NetworkState()
        address = _address(selected) if selected.up else ""
        ssid = ""
        strength = None if selected.kind == IF_TYPE_WIFI else 0
        if selected.kind == IF_TYPE_WIFI and address:
            from docking.applets.network.windows_profiles import read_connections

            try:
                profile = next(
                    (
                        p
                        for p in read_connections()
                        if p.is_wifi
                        and p.identifier.casefold() == selected.identifier.casefold()
                    ),
                    None,
                )
                if profile is not None:
                    ssid, strength = profile.ssid, profile.strength
            except OSError as exc:
                log.debug("Wi-Fi profile metadata unavailable: %s", exc)
        return NetworkState(
            is_connected=selected.up and bool(address),
            is_wifi=selected.kind == IF_TYPE_WIFI,
            iface=selected.name,
            interface_id=selected.identifier,
            ip_address=address,
            ssid=ssid,
            signal_strength=strength,
        )

    def counters(self) -> dict[str, TrafficCounters] | None:
        return self._counters
