"""Windows network selection, stable identity, IP Helper ownership and UI contract."""

import ctypes
import ipaddress
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.network import backend
from docking.applets.network.applet import NetworkApplet
from docking.applets.network.implementations.windows import backend as windows
from docking.applets.network.state import NetworkState, TrafficCounters, build_tooltip
from docking.core.config import Config
from docking.platform import environment
from docking.platform.composition.windows import WindowsAppletBackendFactories
from tests.platform_factories import make_applet_platform


@pytest.fixture(autouse=True)
def profile_metadata(monkeypatch):
    from docking.applets.network.implementations.windows import (
        profiles as windows_profiles,
    )

    read = Mock(return_value=[])
    monkeypatch.setattr(windows_profiles, "read_connections", read)
    return read


def _adapter(identifier="{a}", name="Ethernet", index=6, **values):
    return windows._Adapter(
        identifier,
        name,
        index,
        values.pop("kind", 6),
        values.pop("up", True),
        values.pop("addresses", ("192.0.2.2",)),
        counters=values.pop("counters", TrafficCounters(1000, 2000)),
        **values,
    )


def _api(adapters=(), routes=()):
    return SimpleNamespace(
        adapters=Mock(return_value=list(adapters)),
        routes=Mock(return_value=list(routes)),
    )


def test_auto_selection_combines_route_and_interface_metrics():
    first = _adapter(ipv4_metric=100)
    second = _adapter("{b}", "Wi-Fi", 8, kind=71, ipv4_metric=10)
    api = _api(
        [first, second],
        [windows._DefaultRoute(6, 2, 0), windows._DefaultRoute(8, 2, 50)],
    )
    value = windows.WindowsNetworkBackend(api=api)
    state = value.state()
    assert state.interface_id == "{b}" and state.is_wifi and state.is_connected
    assert value.interface == ""  # Automatic remains a preference, not a saved device.
    api.routes.return_value = [windows._DefaultRoute(6, 2, 0)]
    assert value.state().interface_id == "{a}"


def test_ipv6_default_route_and_address_work_without_ipv4():
    adapter = _adapter(addresses=("fe80::1%6", "2001:db8::2"), ipv6_metric=10)
    value = windows.WindowsNetworkBackend(
        api=_api([adapter], [windows._DefaultRoute(6, 23, 5)])
    )
    assert value.state().ip_address == "2001:db8::2"
    assert value.state().is_connected


def test_explicit_adapter_identity_survives_rename_and_does_not_fall_back_on_removal():
    api = _api([_adapter(), _adapter("{b}", "Other", 8)])
    value = windows.WindowsNetworkBackend("{A}", api=api)
    assert value.state().iface == "Ethernet"
    api.adapters.return_value[0] = replace(api.adapters.return_value[0], name="Réseau")
    assert value.state().iface == "Réseau"
    api.adapters.return_value.pop(0)
    assert value.state() == NetworkState()
    assert value.interface == "{A}"
    api.adapters.return_value.append(_adapter(name="Réseau"))
    assert value.state().is_connected


def test_requested_down_adapter_remains_selected_without_claiming_connection():
    value = windows.WindowsNetworkBackend(
        "{a}", api=_api([_adapter(up=False), _adapter("{b}", "Other", 8)])
    )
    state = value.state()
    assert state.interface_id == "{a}" and not state.is_connected
    assert not state.ip_address


def test_route_query_failure_keeps_local_link_and_adapter_failure_resets_samples():
    api = _api([_adapter()])
    api.routes.side_effect = OSError("route table changed")
    value = windows.WindowsNetworkBackend(api=api)
    assert value.state().is_connected
    assert value.counters() == {"Ethernet": TrafficCounters(1000, 2000)}
    api.adapters.side_effect = OSError("adapter removed")
    assert value.state() == NetworkState() and value.counters() is None
    api.adapters.side_effect = None
    assert value.state().is_connected
    value.stop()
    assert value.counters() is None


def test_interface_choices_use_identifiers_and_offer_auto_selection():
    value = windows.WindowsNetworkBackend(api=_api([_adapter(name="Café")]))
    value.state()
    assert value.interface_choices() == (("Auto", ""), ("Café 192.0.2.2", "{a}"))


def test_windows_settings_actions_use_shell_associations_behind_the_contract(
    monkeypatch,
):
    from docking.platform.native.windows import shell

    launch = Mock(return_value=True)
    monkeypatch.setattr(shell, "open_default_target", launch)
    value = windows.WindowsNetworkBackend(api=_api())
    settings = value.settings
    assert (
        settings.connection_info_available() and settings.edit_connections_available()
    )
    assert settings.open_connection_info()
    assert settings.open_edit_connections()
    assert settings.open_hidden_wifi_settings()
    assert settings.open_new_wifi_settings()
    assert [c.args[0] for c in launch.call_args_list] == [
        "ms-settings:network-status",
        "ms-settings:network-advancedsettings",
        "ms-settings:network-wifisettings",
        "ms-settings:network-mobilehotspot",
    ]
    launch.return_value = False
    assert not settings.open_connection_info()


def test_shared_applet_resets_traffic_when_same_alias_belongs_to_a_new_identity(
    monkeypatch,
):
    import docking.applets.network.applet as module

    monkeypatch.setattr(environment, "is_windows", lambda: True)
    api = _api([_adapter()])
    monkeypatch.setattr(windows, "_ip_helper", lambda: api)
    times = iter([1.0, 3.0, 5.0, 7.0])
    # Replacing the shared time module's function also changes other workers'
    # clocks, which can consume this test's four traffic-sample timestamps.
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(times)))
    config = Config()
    applet = NetworkApplet(
        48,
        config,
        platform_services=make_applet_platform(
            backends=WindowsAppletBackendFactories()
        ),
    )
    assert isinstance(applet._backend, backend.NetworkBackend)
    applet._tick()
    api.adapters.return_value = [
        _adapter("{b}", counters=TrafficCounters(2**40, 2**40))
    ]
    applet._tick()
    assert applet._state.rx_speed == 0 and applet._state.tx_speed == 0
    api.adapters.return_value[0] = replace(
        api.adapters.return_value[0],
        counters=TrafficCounters(2**40 + 2000, 2**40 + 4000),
    )
    applet._tick()
    assert (applet._state.rx_speed, applet._state.tx_speed) == (1000, 2000)
    applet._set_interface("{b}")
    assert config.applet_prefs["network"]["interface"] == "{b}"


def test_wifi_without_accessible_ssid_is_not_labeled_as_ethernet():
    text = build_tooltip(True, "", 0, "Wi-Fi", "192.0.2.2", 0, 0, is_wifi=True)
    assert "Ethernet" not in text and "Network: Wi-Fi" in text


def _sockaddr(address, scope=0):
    ip = ipaddress.ip_address(address)
    raw = bytearray(16 if ip.version == 4 else 28)
    raw[:2] = (2 if ip.version == 4 else 23).to_bytes(2, "little")
    raw[4:8] = ip.packed if ip.version == 4 else raw[4:8]
    if ip.version == 6:
        raw[8:24], raw[24:28] = ip.packed, scope.to_bytes(4, "little")
    return ctypes.create_string_buffer(bytes(raw))


@pytest.mark.parametrize(
    "address,scope,expected",
    [
        ("192.0.2.2", 0, "192.0.2.2"),
        ("2001:db8::2", 0, "2001:db8::2"),
        ("fe80::1", 6, "fe80::1%6"),
        ("127.0.0.1", 0, None),
        ("::1", 0, None),
        ("0.0.0.0", 0, None),
        ("::", 0, None),
    ],
)
def test_native_sockaddr_decoding_keeps_ipv6_scope_and_excludes_loopback(
    address, scope, expected
):
    memory = _sockaddr(address, scope)
    value = windows.SocketAddress(ctypes.addressof(memory), len(memory) - 1)
    assert windows.socket_address(value) == expected


def test_ip_helper_sdk_layout_uses_windows_widths_and_table_alignment():
    assert ctypes.sizeof(windows.InterfaceRow) == 1352
    assert windows.InterfaceRow.in_octets.offset == 1208
    assert windows.InterfaceRow.out_octets.offset == 1280
    assert ctypes.sizeof(windows.RouteRow) == 104
    assert windows.RouteTable.rows.offset == 8


class _Kernel:
    def __init__(self, monkeypatch):
        self.socket = _sockaddr("192.0.2.2")
        self.unicast = windows.UnicastAddress(
            alignment=ctypes.sizeof(windows.UnicastAddress),
            dad_state=4,
            socket=windows.SocketAddress(ctypes.addressof(self.socket), 16),
        )
        self.adapter = windows.AdapterAddress(
            length=ctypes.sizeof(windows.AdapterAddress),
            index=6,
            ipv6_index=6,
            identifier=b"{GUID}",
            name="Café",
            kind=6,
            oper_status=1,
            unicast=ctypes.pointer(self.unicast),
            luid=0x600,
            ipv4_metric=15,
        )

        class Table(ctypes.Structure):
            _fields_ = [("count", ctypes.c_uint32), ("rows", windows.RouteRow * 2)]

        self.table = Table(count=2)
        self.table.rows[0].index = 6
        self.table.rows[0].valid_lifetime = 0xFFFFFFFF
        self.table.rows[0].destination.address.family = 2
        self.table.rows[1].destination.length = 24
        self.resized = False
        self.status = 0
        self.dll = SimpleNamespace(
            GetAdaptersAddresses=Mock(side_effect=self.adapters),
            GetIfEntry2=Mock(side_effect=self.interface),
            GetIpForwardTable2=Mock(side_effect=self.routes),
            FreeMibTable=Mock(),
        )
        monkeypatch.setattr(
            windows.ctypes, "WinDLL", lambda *_a, **_kw: self.dll, raising=False
        )
        self.api = windows._IPHelper()

    def adapters(self, family, flags, reserved, output, size):
        assert (family, flags, reserved) == (0, 2 | 4 | 8, None)
        if not self.resized:
            size._obj.value = 32768
            self.resized = True
            return 111
        if not self.status:
            ctypes.memmove(
                output, ctypes.byref(self.adapter), ctypes.sizeof(self.adapter)
            )
        return self.status

    def interface(self, output):
        assert output._obj.index == 0 and output._obj.luid == 0x600
        output._obj.in_octets = 2**40 + 1000
        output._obj.out_octets = 2**40 + 2000
        return 0

    def routes(self, family, output):
        assert family == 0
        output._obj.value = ctypes.addressof(self.table)
        return 0


def test_adapter_buffer_resize_reads_metadata_and_full_64bit_counters(monkeypatch):
    kernel = _Kernel(monkeypatch)
    assert kernel.api.adapters() == [
        windows._Adapter(
            "{guid}",
            "Café",
            6,
            6,
            True,
            ("192.0.2.2",),
            15,
            0,
            TrafficCounters(2**40 + 1000, 2**40 + 2000),
        )
    ]
    assert kernel.dll.GetAdaptersAddresses.call_count == 2


def test_routes_are_copied_and_native_allocation_freed_on_success_and_validation_error(
    monkeypatch,
):
    kernel = _Kernel(monkeypatch)
    assert kernel.api.routes() == [windows._DefaultRoute(6, 2, 0)]
    assert kernel.dll.FreeMibTable.call_count == 1
    kernel.table.count = 65537
    with pytest.raises(OSError, match="count"):
        kernel.api.routes()
    assert kernel.dll.FreeMibTable.call_count == 2


def test_empty_and_failed_adapter_queries_are_distinct(monkeypatch):
    kernel = _Kernel(monkeypatch)
    kernel.status = 232
    assert kernel.api.adapters() == []
    kernel.status = 5
    with pytest.raises(OSError):
        kernel.api.adapters()


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows IP Helper")
def test_native_read_only_snapshot_and_counters():
    value = windows.WindowsNetworkBackend()
    state = value.state()
    if state.iface:
        assert state.interface_id
        assert state.iface in (value.counters() or {})
        assert any(
            key == state.interface_id for _label, key in value.interface_choices()
        )


def test_wifi_profile_metadata_matches_persistent_adapter_identity(profile_metadata):
    from docking.applets.network.implementations.windows.profiles import (
        ConnectionProfile,
    )

    profile_metadata.return_value = [
        ConnectionProfile("{other}", True, "Wrong network", 100),
        ConnectionProfile("{A}", True, " Café ", 80),
    ]
    api = _api([_adapter(kind=71)])
    value = windows.WindowsNetworkBackend(api=api)
    state = value.state()
    assert (state.ssid, state.signal_strength) == (" Café ", 80)
    assert state.interface_id == "{a}" and state.is_connected
    api.adapters.return_value[0] = replace(api.adapters.return_value[0], up=False)
    assert not value.state().ssid
    assert profile_metadata.call_count == 1


def test_wifi_metadata_failure_preserves_link_without_fake_signal(profile_metadata):
    profile_metadata.side_effect = OSError("Network profile changed")
    value = windows.WindowsNetworkBackend(api=_api([_adapter(kind=71)]))
    state = value.state()
    assert state.is_connected and state.ip_address == "192.0.2.2"
    assert not state.ssid and state.signal_strength is None
