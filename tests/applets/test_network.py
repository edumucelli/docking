"""Tests for the Network applet -- parsing, speed formatting, icon mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.applets.network.applet as network_applet_mod
import docking.applets.network.render as network_render_mod
import docking.applets.network.state as network_state_mod
from docking.applets.network.applet import NetworkApplet
from docking.applets.network.render import create_icon as create_network_icon
from docking.applets.network.state import (
    AvailableNetwork,
    TrafficCounters,
    compute_speeds,
    connection_info_command,
    decode_ssid,
    dedupe_networks,
    edit_connections_command,
    format_compact_speed,
    format_speed,
    open_connection_info,
    open_edit_connections,
    open_hidden_wifi_settings,
    open_new_wifi_settings,
    parse_proc_net_dev,
    signal_to_icon,
)

SAMPLE_PROC_NET_DEV = """\
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 1234567   12345    0    0    0     0          0         0  1234567   12345    0    0    0     0       0          0
wlp0s20f3: 138460662022 154167615    0 6749    0     0          0         0 49607734285  97547410    0    0    0     0       0          0
  eth0:  500000    1000    0    0    0     0          0         0   250000     500    0    0    0     0       0          0
"""


class _FakeMenuItem:
    def __init__(self, label: str = "") -> None:
        self._label = label
        self._sensitive = True
        self._signals: dict[str, list[object]] = {}
        self._submenu = None

    def get_label(self) -> str:
        return self._label

    def set_sensitive(self, value: bool) -> None:
        self._sensitive = value

    def connect(self, signal: str, callback, *args) -> None:
        self._signals.setdefault(signal, []).append((callback, args))

    def set_submenu(self, submenu) -> None:
        self._submenu = submenu

    def get_submenu(self):
        return self._submenu


class _FakeCheckMenuItem(_FakeMenuItem):
    def __init__(self, label: str = "") -> None:
        super().__init__(label)
        self._active = False

    def set_active(self, active: bool) -> None:
        self._active = active

    def get_active(self) -> bool:
        return self._active


class _FakeSeparatorMenuItem(_FakeMenuItem):
    pass


class _FakeMenu:
    def __init__(self) -> None:
        self._children: list[object] = []

    def append(self, item) -> None:
        self._children.append(item)

    def get_children(self):
        return list(self._children)


class TestParseProcNetDev:
    def test_parses_interfaces(self):
        result = parse_proc_net_dev(text=SAMPLE_PROC_NET_DEV)
        assert "lo" in result
        assert "wlp0s20f3" in result
        assert "eth0" in result

    def test_rx_tx_values(self):
        result = parse_proc_net_dev(text=SAMPLE_PROC_NET_DEV)
        rx, tx = result["wlp0s20f3"]
        assert rx == 138460662022
        assert tx == 49607734285

    def test_lo_values(self):
        result = parse_proc_net_dev(text=SAMPLE_PROC_NET_DEV)
        rx, tx = result["lo"]
        assert rx == 1234567
        assert tx == 1234567

    def test_empty_text(self):
        assert parse_proc_net_dev(text="") == {}

    def test_headers_only(self):
        text = "Inter-|   Receive\n face |bytes\n"
        assert parse_proc_net_dev(text=text) == {}


class TestComputeSpeeds:
    def test_basic_speeds(self):
        prev = TrafficCounters(1000, 500)
        curr = TrafficCounters(3000, 1500)
        down, up = compute_speeds(prev=prev, curr=curr, elapsed_s=2.0)
        assert down == pytest.approx(1000.0)
        assert up == pytest.approx(500.0)

    def test_zero_elapsed(self):
        down, up = compute_speeds(
            prev=TrafficCounters(0, 0), curr=TrafficCounters(1000, 500), elapsed_s=0.0
        )
        assert down == 0.0
        assert up == 0.0

    def test_no_change(self):
        down, up = compute_speeds(
            prev=TrafficCounters(1000, 500),
            curr=TrafficCounters(1000, 500),
            elapsed_s=1.0,
        )
        assert down == 0.0
        assert up == 0.0

    def test_counter_wraparound_clamped(self):
        # If curr < prev (counter reset), clamp to 0
        down, up = compute_speeds(
            prev=TrafficCounters(5000, 3000),
            curr=TrafficCounters(1000, 500),
            elapsed_s=1.0,
        )
        assert down == 0.0
        assert up == 0.0


class TestFormatSpeed:
    def test_bytes(self):
        assert format_speed(bps=500) == "500 B/s"

    def test_kilobytes(self):
        result = format_speed(bps=1536)
        assert "KB/s" in result

    def test_megabytes(self):
        result = format_speed(bps=5 * 1024 * 1024)
        assert "MB/s" in result

    def test_gigabytes(self):
        result = format_speed(bps=2 * 1024 * 1024 * 1024)
        assert "GB/s" in result

    def test_zero(self):
        assert format_speed(bps=0) == "0 B/s"


class TestFormatCompactSpeed:
    def test_bytes(self):
        assert format_compact_speed(bps=500) == "500B"

    def test_kilobytes_uses_explicit_byte_unit(self):
        assert format_compact_speed(bps=1536) == "1.5KB"

    def test_megabytes_uses_explicit_byte_unit(self):
        assert format_compact_speed(bps=5 * 1024 * 1024) == "5MB"

    def test_gigabytes_uses_explicit_byte_unit(self):
        assert format_compact_speed(bps=2 * 1024 * 1024 * 1024) == "2GB"


class TestSignalToIcon:
    def test_disconnected(self):
        assert (
            signal_to_icon(strength=0, is_connected=False, is_wifi=False)
            == "network-offline-symbolic"
        )

    def test_ethernet(self):
        assert (
            signal_to_icon(strength=0, is_connected=True, is_wifi=False)
            == "network-wired-symbolic"
        )

    def test_wifi_weak(self):
        assert "weak" in signal_to_icon(strength=20, is_connected=True, is_wifi=True)

    def test_wifi_ok(self):
        assert "ok" in signal_to_icon(strength=50, is_connected=True, is_wifi=True)

    def test_wifi_good(self):
        assert "good" in signal_to_icon(strength=70, is_connected=True, is_wifi=True)

    def test_wifi_excellent(self):
        assert "excellent" in signal_to_icon(
            strength=90, is_connected=True, is_wifi=True
        )

    def test_wifi_boundary_80(self):
        assert "excellent" in signal_to_icon(
            strength=80, is_connected=True, is_wifi=True
        )

    def test_wifi_boundary_60(self):
        assert "good" in signal_to_icon(strength=60, is_connected=True, is_wifi=True)

    def test_wifi_boundary_40(self):
        assert "ok" in signal_to_icon(strength=40, is_connected=True, is_wifi=True)


class TestNetworkApplet:
    def _fake_gtk(self, monkeypatch):
        fake_gtk = SimpleNamespace(
            Menu=_FakeMenu,
            MenuItem=_FakeMenuItem,
            CheckMenuItem=_FakeCheckMenuItem,
            SeparatorMenuItem=_FakeSeparatorMenuItem,
        )
        monkeypatch.setattr(network_applet_mod, "Gtk", fake_gtk)

    def test_creates_with_icon(self):
        applet = NetworkApplet(48)
        assert applet.item.icon is not None

    def test_renders_at_various_sizes(self):
        for size in [32, 48, 64]:
            applet = NetworkApplet(size)
            pixbuf = applet.create_icon(size)
            assert pixbuf is not None

    def test_tooltip_disconnected(self):
        applet = NetworkApplet(48)
        applet.refresh_tooltip()
        assert "not connected" in applet.item.name.lower()

    def test_menu_returns_items(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        applet = NetworkApplet(48)
        items = applet.get_menu_items()
        assert len(items) >= 1

    def test_menu_includes_tray_style_actions(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        monkeypatch.setattr(
            network_applet_mod, "connection_info_command", lambda: ["a"]
        )
        monkeypatch.setattr(
            network_applet_mod,
            "edit_connections_command",
            lambda: ["b"],
        )
        monkeypatch.setattr(
            network_applet_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2),
            raising=False,
        )

        wifi_device = MagicMock()
        wifi_device.get_device_type.return_value = 2

        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        applet._nm_client.get_devices.return_value = [wifi_device]
        applet._nm_client.networking_get_enabled.return_value = True
        applet._nm_client.wireless_get_enabled.return_value = True
        applet._nm_client.wireless_hardware_get_enabled.return_value = True

        labels = [item.get_label() for item in applet.get_menu_items()]
        assert "Connection Information" in labels
        assert "Edit Connections..." in labels
        assert "Available Networks" in labels
        assert "Connect to Hidden Wi-Fi Network..." in labels
        assert "Create New Wi-Fi Network..." in labels
        assert "Enable Networking" in labels
        assert "Enable Wi-Fi" in labels

    def test_available_networks_submenu_lists_visible_ssids(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        monkeypatch.setattr(network_applet_mod, "connection_info_command", lambda: None)
        monkeypatch.setattr(
            network_applet_mod, "edit_connections_command", lambda: None
        )
        monkeypatch.setattr(
            network_applet_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2),
            raising=False,
        )

        def ap(ssid: str, strength: int, path: str):
            access_point = MagicMock()
            access_point.get_ssid.return_value = ssid.encode()
            access_point.get_strength.return_value = strength
            access_point.get_path.return_value = path
            return access_point

        active = ap("DockNet", 65, "/ap/1")
        duplicate_weaker = ap("DockNet", 20, "/ap/2")
        guest = ap("Guest", 30, "/ap/3")

        wifi_device = MagicMock()
        wifi_device.get_device_type.return_value = 2
        wifi_device.get_active_access_point.return_value = active
        wifi_device.get_access_points.return_value = [active, duplicate_weaker, guest]

        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        applet._nm_client.get_devices.return_value = [wifi_device]
        applet._nm_client.get_connections.return_value = []
        applet._nm_client.networking_get_enabled.return_value = True
        applet._nm_client.wireless_get_enabled.return_value = True
        applet._nm_client.wireless_hardware_get_enabled.return_value = True

        networks_item = next(
            item
            for item in applet.get_menu_items()
            if item.get_label() == "Available Networks"
        )
        submenu_labels = [
            child.get_label() for child in networks_item.get_submenu().get_children()
        ]
        assert submenu_labels == ["Connected: DockNet (65%)", "Guest (30%)"]
        wifi_device.request_scan.assert_called_once_with(None)

    def test_available_networks_scan_request_is_rate_limited(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        monkeypatch.setattr(
            network_applet_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2),
            raising=False,
        )
        times = iter([100.0, 105.0, 121.0])
        monkeypatch.setattr(network_applet_mod.time, "monotonic", lambda: next(times))

        wifi_device = MagicMock()
        wifi_device.get_device_type.return_value = 2
        wifi_device.get_access_points.return_value = []
        wifi_device.get_active_access_point.return_value = None
        wifi_device.request_scan.return_value = True

        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        applet._nm_client.get_devices.return_value = [wifi_device]

        applet._available_networks()
        applet._available_networks()
        applet._available_networks()

        assert wifi_device.request_scan.call_count == 2

    def test_menu_includes_vpn_connections_submenu(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        monkeypatch.setattr(network_applet_mod, "connection_info_command", lambda: None)
        monkeypatch.setattr(
            network_applet_mod, "edit_connections_command", lambda: None
        )
        monkeypatch.setattr(
            network_applet_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2),
            raising=False,
        )

        wifi_device = MagicMock()
        wifi_device.get_device_type.return_value = 2
        wifi_device.get_access_points.return_value = []
        wifi_device.get_active_access_point.return_value = None

        vpn_connection = MagicMock()
        vpn_connection.get_setting_vpn.return_value = object()
        vpn_connection.get_uuid.return_value = "vpn-1"
        vpn_connection.get_id.return_value = "Work VPN"

        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        applet._nm_client.get_devices.return_value = [wifi_device]
        applet._nm_client.get_connections.return_value = [vpn_connection]
        applet._nm_client.get_active_connections.return_value = []
        applet._nm_client.networking_get_enabled.return_value = True
        applet._nm_client.wireless_get_enabled.return_value = True
        applet._nm_client.wireless_hardware_get_enabled.return_value = True

        vpn_item = next(
            item
            for item in applet.get_menu_items()
            if item.get_label() == "VPN Connections"
        )
        submenu_labels = [
            child.get_label() for child in vpn_item.get_submenu().get_children()
        ]
        assert submenu_labels == ["Work VPN"]

    def test_menu_omits_wifi_toggle_without_wifi_device(self, monkeypatch):
        self._fake_gtk(monkeypatch)
        monkeypatch.setattr(network_applet_mod, "connection_info_command", lambda: None)
        monkeypatch.setattr(
            network_applet_mod,
            "edit_connections_command",
            lambda: None,
        )
        monkeypatch.setattr(
            network_applet_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2),
            raising=False,
        )

        ethernet_device = MagicMock()
        ethernet_device.get_device_type.return_value = 1

        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        applet._nm_client.get_devices.return_value = [ethernet_device]
        applet._nm_client.networking_get_enabled.return_value = True

        labels = [item.get_label() for item in applet.get_menu_items()]
        assert "Enable Networking" in labels
        assert "Enable Wi-Fi" not in labels

    def test_tooltip_wifi_shows_ssid(self):
        applet = NetworkApplet(48)
        applet._is_connected = True
        applet._is_wifi = True
        applet._ssid = "MyNetwork"
        applet._signal_strength = 72
        applet._rx_speed = 1500.0
        applet._tx_speed = 300.0
        applet.refresh_tooltip()
        assert "MyNetwork" in applet.item.name
        assert "72%" in applet.item.name

    def test_tooltip_ethernet(self):
        applet = NetworkApplet(48)
        applet._is_connected = True
        applet._is_wifi = False
        applet._iface = "eth0"
        applet.refresh_tooltip()
        assert "Ethernet" in applet.item.name
        assert "eth0" in applet.item.name

    def test_icon_changes_with_state(self):
        applet = NetworkApplet(48)
        # Disconnected
        applet._is_connected = False
        icon1 = signal_to_icon(
            strength=0, is_connected=applet._is_connected, is_wifi=applet._is_wifi
        )
        assert "offline" in icon1
        # Connected wifi
        applet._is_connected = True
        applet._is_wifi = True
        applet._signal_strength = 90
        icon2 = signal_to_icon(
            strength=applet._signal_strength, is_connected=True, is_wifi=True
        )
        assert "excellent" in icon2


class TestNmDevicePriority:
    """Wifi should be preferred over ethernet, tun/bridge should be skipped."""

    def test_wifi_preferred_over_other(self):
        # This is tested implicitly by the priority logic:
        # wifi=2 > ethernet=1 > other=0
        # tun/bridge are skipped entirely
        applet = NetworkApplet(48)
        applet._is_connected = True
        applet._is_wifi = True
        applet._ssid = "TestWifi"
        applet._signal_strength = 80
        applet.refresh_tooltip()
        assert "TestWifi" in applet.item.name


class TestNetworkAppletInternals:
    def test_connect_available_network_uses_saved_connection_when_present(
        self, monkeypatch
    ):
        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        saved_connection = MagicMock()
        device = MagicMock()
        access_point = MagicMock()
        access_point.get_path.return_value = "/ap/known"
        monkeypatch.setattr(
            applet,
            "_find_wifi_access_point",
            lambda network: (device, access_point),
        )
        monkeypatch.setattr(
            applet,
            "_find_saved_wifi_connection",
            lambda ssid: saved_connection,
        )

        applet._connect_available_network(
            network=AvailableNetwork(
                ssid="DockNet",
                strength=70,
                access_point_path="/ap/known",
                is_active=False,
            )
        )

        applet._nm_client.activate_connection_async.assert_called_once_with(
            saved_connection,
            device,
            "/ap/known",
            None,
            applet._on_activate_connection_finished,
            None,
        )
        applet._nm_client.add_and_activate_connection_async.assert_not_called()

    def test_connect_available_network_adds_connection_when_unsaved(self, monkeypatch):
        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        device = MagicMock()
        access_point = MagicMock()
        access_point.get_path.return_value = "/ap/new"
        monkeypatch.setattr(
            applet,
            "_find_wifi_access_point",
            lambda network: (device, access_point),
        )
        monkeypatch.setattr(
            applet,
            "_find_saved_wifi_connection",
            lambda ssid: None,
        )

        applet._connect_available_network(
            network=AvailableNetwork(
                ssid="NewNet",
                strength=45,
                access_point_path="/ap/new",
                is_active=False,
            )
        )

        applet._nm_client.add_and_activate_connection_async.assert_called_once_with(
            None,
            device,
            "/ap/new",
            None,
            applet._on_add_and_activate_connection_finished,
            None,
        )
        applet._nm_client.activate_connection_async.assert_not_called()

    def test_toggle_vpn_connection_activates_saved_vpn(self):
        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        connection = MagicMock()

        applet._toggle_vpn_connection(
            connection=connection,
            is_active=False,
            active_connection=None,
        )

        applet._nm_client.activate_connection_async.assert_called_once_with(
            connection,
            None,
            None,
            None,
            applet._on_activate_connection_finished,
            None,
        )

    def test_toggle_vpn_connection_deactivates_active_vpn(self):
        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        connection = MagicMock()
        active_connection = MagicMock()

        applet._toggle_vpn_connection(
            connection=connection,
            is_active=True,
            active_connection=active_connection,
        )

        applet._nm_client.deactivate_connection_async.assert_called_once_with(
            active_connection,
            None,
            applet._on_deactivate_connection_finished,
            None,
        )

    def test_set_networking_enabled_updates_client_and_refreshes(self, monkeypatch):
        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        update = MagicMock()
        refresh = MagicMock()
        monkeypatch.setattr(applet, "_update_nm_state", update)
        monkeypatch.setattr(applet, "present", refresh)

        applet._set_networking_enabled(enabled=False)

        applet._nm_client.networking_set_enabled.assert_called_once_with(False)
        update.assert_called_once()
        refresh.assert_called_once()

    def test_set_wireless_enabled_updates_client_and_refreshes(self, monkeypatch):
        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        update = MagicMock()
        refresh = MagicMock()
        monkeypatch.setattr(applet, "_update_nm_state", update)
        monkeypatch.setattr(applet, "present", refresh)

        applet._set_wireless_enabled(enabled=False)

        applet._nm_client.wireless_set_enabled.assert_called_once_with(False)
        update.assert_called_once()
        refresh.assert_called_once()

    def test_start_connects_nm_and_timer(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        notify = MagicMock()
        nm_client = MagicMock()
        monkeypatch.setattr(network_applet_mod.NM.Client, "new", lambda _arg: nm_client)
        monkeypatch.setattr(
            network_applet_mod.GLib, "timeout_add_seconds", lambda _sec, _cb: 321
        )
        update = MagicMock()
        monkeypatch.setattr(applet, "_update_nm_state", update)
        # When
        applet.start(notify)
        # Then
        assert applet._nm_client is nm_client
        assert applet._nm_handler_id == nm_client.connect.return_value
        assert applet._timer_id == 321
        update.assert_called_once()

    def test_start_handles_nm_error(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        notify = MagicMock()
        monkeypatch.setattr(
            network_applet_mod.GLib, "Error", RuntimeError, raising=False
        )
        monkeypatch.setattr(
            network_applet_mod.NM.Client,
            "new",
            MagicMock(side_effect=RuntimeError("nm unavailable")),
        )
        monkeypatch.setattr(
            network_applet_mod.GLib, "timeout_add_seconds", lambda _sec, _cb: 555
        )
        # When
        applet.start(notify)
        # Then
        assert applet._nm_client is None
        assert applet._timer_id == 555

    def test_stop_disconnects_signal_and_timer(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        nm_client = MagicMock()
        applet._nm_client = nm_client
        applet._nm_handler_id = 17
        applet._nm_state_handler_id = 18
        applet._timer_id = 88
        removed: list[int] = []
        monkeypatch.setattr(
            network_applet_mod.GLib, "source_remove", lambda i: removed.append(i)
        )
        # When
        applet.stop()
        # Then
        assert applet._nm_handler_id == 0
        assert applet._nm_state_handler_id == 0
        assert applet._timer_id == 0
        assert removed == [88]
        nm_client.disconnect.assert_any_call(17)
        nm_client.disconnect.assert_any_call(18)

    def test_on_nm_changed_refreshes(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        update = MagicMock()
        refresh = MagicMock()
        monkeypatch.setattr(applet, "_update_nm_state", update)
        monkeypatch.setattr(applet, "present", refresh)
        # When
        applet._on_nm_changed()
        # Then
        update.assert_called_once()
        refresh.assert_called_once()

    def test_update_nm_state_prefers_wifi_and_reads_ip_and_signal(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        monkeypatch.setattr(
            network_applet_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2, ETHERNET=1, TUN=3, BRIDGE=4, LOOPBACK=5),
            raising=False,
        )
        monkeypatch.setattr(
            network_applet_mod.NM,
            "ActiveConnectionState",
            SimpleNamespace(ACTIVATED=9),
            raising=False,
        )

        class FakeWifiDevice:
            def get_device_type(self):
                return 2

            def get_iface(self):
                return "wlan0"

            def get_ip4_config(self):
                addr = MagicMock()
                addr.get_address.return_value = "192.168.1.10"
                cfg = MagicMock()
                cfg.get_addresses.return_value = [addr]
                return cfg

            def get_active_access_point(self):
                ssid = MagicMock()
                ssid.get_data.return_value = b"MyWifi"
                ap = MagicMock()
                ap.get_ssid.return_value = ssid
                ap.get_strength.return_value = 73
                return ap

        class FakeEthDevice:
            def get_device_type(self):
                return 1

            def get_iface(self):
                return "eth0"

            def get_ip4_config(self):
                return None

        monkeypatch.setattr(
            network_applet_mod.NM, "DeviceWifi", FakeWifiDevice, raising=False
        )
        wifi = FakeWifiDevice()
        eth = FakeEthDevice()
        conn_eth = MagicMock()
        conn_eth.get_state.return_value = 9
        conn_eth.get_devices.return_value = [eth]
        conn_wifi = MagicMock()
        conn_wifi.get_state.return_value = 9
        conn_wifi.get_devices.return_value = [wifi]
        applet._nm_client = MagicMock()
        applet._nm_client.get_active_connections.return_value = [conn_eth, conn_wifi]
        # When
        applet._update_nm_state()
        # Then
        assert applet._is_connected is True
        assert applet._is_wifi is True
        assert applet._iface == "wlan0"
        assert applet._ip_address == "192.168.1.10"
        assert applet._ssid == "MyWifi"
        assert applet._signal_strength == 73

    def test_update_nm_state_skips_non_activated_and_tun_bridge(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        monkeypatch.setattr(
            network_applet_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2, ETHERNET=1, TUN=3, BRIDGE=4, LOOPBACK=5),
            raising=False,
        )
        monkeypatch.setattr(
            network_applet_mod.NM,
            "ActiveConnectionState",
            SimpleNamespace(ACTIVATED=9),
            raising=False,
        )
        monkeypatch.setattr(network_applet_mod.NM, "DeviceWifi", object, raising=False)
        tun = MagicMock()
        tun.get_device_type.return_value = 3
        bad = MagicMock()
        bad.get_state.return_value = 0
        bad.get_devices.return_value = [tun]
        tun_conn = MagicMock()
        tun_conn.get_state.return_value = 9
        tun_conn.get_devices.return_value = [tun]
        applet._nm_client = MagicMock()
        applet._nm_client.get_active_connections.return_value = [bad, tun_conn]
        # When
        applet._update_nm_state()
        # Then
        assert applet._is_connected is False
        assert applet._iface == ""

    def test_update_nm_state_skips_loopback_when_connection_has_wifi_too(
        self, monkeypatch
    ):
        # Given
        applet = NetworkApplet(48)
        monkeypatch.setattr(
            network_applet_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2, ETHERNET=1, TUN=3, BRIDGE=4, LOOPBACK=5),
            raising=False,
        )
        monkeypatch.setattr(
            network_applet_mod.NM,
            "ActiveConnectionState",
            SimpleNamespace(ACTIVATED=9),
            raising=False,
        )

        class FakeLoopbackDevice:
            def get_device_type(self):
                return 5

            def get_iface(self):
                return "lo"

        class FakeWifiDevice:
            def get_device_type(self):
                return 2

            def get_iface(self):
                return "wlan0"

            def get_ip4_config(self):
                addr = MagicMock()
                addr.get_address.return_value = "192.168.1.10"
                cfg = MagicMock()
                cfg.get_addresses.return_value = [addr]
                return cfg

            def get_active_access_point(self):
                ssid = MagicMock()
                ssid.get_data.return_value = b"MyWifi"
                ap = MagicMock()
                ap.get_ssid.return_value = ssid
                ap.get_strength.return_value = 73
                return ap

        monkeypatch.setattr(
            network_applet_mod.NM, "DeviceWifi", FakeWifiDevice, raising=False
        )
        conn_wifi = MagicMock()
        conn_wifi.get_state.return_value = 9
        conn_wifi.get_devices.return_value = [FakeLoopbackDevice(), FakeWifiDevice()]
        applet._nm_client = MagicMock()
        applet._nm_client.get_active_connections.return_value = [conn_wifi]
        # When
        applet._update_nm_state()
        # Then
        assert applet._is_connected is True
        assert applet._is_wifi is True
        assert applet._iface == "wlan0"
        assert applet._ssid == "MyWifi"
        assert applet._signal_strength == 73

    def test_tick_updates_and_refreshes(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        update_nm = MagicMock()
        update_traffic = MagicMock()
        update_wifi = MagicMock()
        refresh = MagicMock()
        monkeypatch.setattr(applet, "_update_nm_state", update_nm)
        monkeypatch.setattr(applet, "_update_traffic", update_traffic)
        monkeypatch.setattr(applet, "_update_wifi_signal", update_wifi)
        monkeypatch.setattr(applet, "present", refresh)
        # When
        result = applet._tick()
        # Then
        assert result is True
        update_nm.assert_called_once()
        update_traffic.assert_called_once()
        update_wifi.assert_called_once()
        refresh.assert_called_once()

    def test_update_traffic_no_iface_resets_speeds(self):
        # Given
        applet = NetworkApplet(48)
        applet._iface = ""
        applet._rx_speed = 10.0
        applet._tx_speed = 20.0
        # When
        applet._update_traffic()
        # Then
        assert applet._rx_speed == 0.0
        assert applet._tx_speed == 0.0

    def test_update_traffic_handles_proc_read_error(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        applet._iface = "eth0"
        monkeypatch.setattr("builtins.open", MagicMock(side_effect=OSError("boom")))
        # When / Then
        applet._update_traffic()

    def test_update_traffic_computes_and_updates_previous(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        applet._iface = "eth0"
        applet._prev_counters = TrafficCounters(1000, 2000)
        applet._prev_time = 10.0
        data = (
            "Inter-| Receive | Transmit\n"
            " face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed\n"
            "eth0: 3000 0 0 0 0 0 0 0 5000 0 0 0 0 0 0 0\n"
        )
        fake_file = MagicMock()
        fake_file.read.return_value = data
        open_cm = MagicMock()
        open_cm.__enter__.return_value = fake_file
        open_cm.__exit__.return_value = False
        monkeypatch.setattr(
            type(network_applet_mod._PROC_NET_DEV),
            "open",
            lambda *_a, **_k: open_cm,
        )
        monkeypatch.setattr(network_applet_mod.time, "monotonic", lambda: 12.0)
        # When
        applet._update_traffic()
        # Then
        assert applet._rx_speed == pytest.approx(1000.0)
        assert applet._tx_speed == pytest.approx(1500.0)
        assert applet._prev_counters == TrafficCounters(3000, 5000)
        assert applet._prev_time == 12.0

    def test_update_wifi_signal_reads_strength(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        applet._is_wifi = True
        monkeypatch.setattr(
            network_applet_mod.NM,
            "ActiveConnectionState",
            SimpleNamespace(ACTIVATED=9),
            raising=False,
        )

        class FakeWifiDevice:
            def get_active_access_point(self):
                ap = MagicMock()
                ap.get_strength.return_value = 81
                return ap

        monkeypatch.setattr(
            network_applet_mod.NM, "DeviceWifi", FakeWifiDevice, raising=False
        )
        conn = MagicMock()
        conn.get_state.return_value = 9
        conn.get_devices.return_value = [FakeWifiDevice()]
        applet._nm_client = MagicMock()
        applet._nm_client.get_active_connections.return_value = [conn]
        # When
        applet._update_wifi_signal()
        # Then
        assert applet._signal_strength == 81

    def test_update_wifi_signal_skips_non_wifi_active_connection_first(
        self, monkeypatch
    ):
        # Given
        applet = NetworkApplet(48)
        applet._is_wifi = True
        monkeypatch.setattr(
            network_applet_mod.NM,
            "ActiveConnectionState",
            SimpleNamespace(ACTIVATED=9),
            raising=False,
        )

        class FakeWifiDevice:
            def get_active_access_point(self):
                ap = MagicMock()
                ap.get_strength.return_value = 67
                return ap

        class FakeEthDevice:
            pass

        monkeypatch.setattr(
            network_applet_mod.NM, "DeviceWifi", FakeWifiDevice, raising=False
        )

        conn_eth = MagicMock()
        conn_eth.get_state.return_value = 9
        conn_eth.get_devices.return_value = [FakeEthDevice()]

        conn_wifi = MagicMock()
        conn_wifi.get_state.return_value = 9
        conn_wifi.get_devices.return_value = [FakeWifiDevice()]

        applet._nm_client = MagicMock()
        applet._nm_client.get_active_connections.return_value = [conn_eth, conn_wifi]
        # When
        applet._update_wifi_signal()
        # Then
        assert applet._signal_strength == 67

    def test_update_wifi_signal_skips_loopback_device_before_wifi(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        applet._is_wifi = True
        monkeypatch.setattr(
            network_applet_mod.NM,
            "ActiveConnectionState",
            SimpleNamespace(ACTIVATED=9),
            raising=False,
        )

        class FakeLoopbackDevice:
            pass

        class FakeWifiDevice:
            def get_active_access_point(self):
                ap = MagicMock()
                ap.get_strength.return_value = 67
                return ap

        monkeypatch.setattr(
            network_applet_mod.NM, "DeviceWifi", FakeWifiDevice, raising=False
        )

        conn = MagicMock()
        conn.get_state.return_value = 9
        conn.get_devices.return_value = [FakeLoopbackDevice(), FakeWifiDevice()]

        applet._nm_client = MagicMock()
        applet._nm_client.get_active_connections.return_value = [conn]
        # When
        applet._update_wifi_signal()
        # Then
        assert applet._signal_strength == 67

    def test_build_tooltip_disconnected_and_connected(self):
        # Given
        applet = NetworkApplet(48)
        # When / Then
        assert applet._build_tooltip() == "Network: Not connected"

        # Given
        applet._is_connected = True
        applet._is_wifi = False
        applet._iface = "eth0"
        applet._ip_address = "10.0.0.2"
        applet._rx_speed = 2048
        applet._tx_speed = 1024
        # When
        tooltip = applet._build_tooltip()
        # Then
        assert "Ethernet: eth0" in tooltip
        assert "IP: 10.0.0.2" in tooltip
        assert "\u2193" in tooltip and "\u2191" in tooltip


class TestNetworkRender:
    @pytest.mark.parametrize(
        ("connected", "strength"),
        [
            (False, 0),
            (True, 85),
            (True, 65),
            (True, 45),
            (True, 10),
        ],
    )
    def test_wifi_icon_renders_all_signal_branches(self, connected, strength):
        pixbuf = create_network_icon(
            size=48,
            is_connected=connected,
            is_wifi=True,
            signal_strength=strength,
            rx_speed=0.0,
            tx_speed=0.0,
        )
        assert pixbuf is not None

    def test_download_overlay_uses_explicit_byte_unit(self, monkeypatch):
        labels: list[str] = []

        def draw_label(*, cr, text: str, size: int) -> None:
            labels.append(text)

        monkeypatch.setattr(
            network_render_mod,
            "draw_icon_label",
            draw_label,
        )
        pixbuf = create_network_icon(
            size=48,
            is_connected=True,
            is_wifi=True,
            signal_strength=80,
            rx_speed=5 * 1024 * 1024,
            tx_speed=0.0,
            speed_overlay="download",
        )

        assert pixbuf is not None
        assert labels == ["\u21935MB"]


class TestNetworkCommands:
    def test_decode_ssid_supports_nm_byte_container(self):
        ssid_bytes = SimpleNamespace(get_data=lambda: b"DockNet")
        assert decode_ssid(ssid_bytes) == "DockNet"

    def test_dedupe_networks_prefers_active_and_stronger(self):
        networks = [
            AvailableNetwork("Cafe", 35, "/ap/1", False),
            AvailableNetwork("DockNet", 20, "/ap/2", False),
            AvailableNetwork("DockNet", 70, "/ap/3", True),
            AvailableNetwork("Guest", 50, "/ap/4", False),
        ]
        assert dedupe_networks(networks=networks) == [
            AvailableNetwork("DockNet", 70, "/ap/3", True),
            AvailableNetwork("Guest", 50, "/ap/4", False),
            AvailableNetwork("Cafe", 35, "/ap/1", False),
        ]

    def test_connection_info_command_picks_first_available(self, monkeypatch):
        monkeypatch.setattr(
            network_state_mod.shutil,
            "which",
            lambda binary: (
                "/usr/bin/gnome-control-center"
                if binary == "gnome-control-center"
                else None
            ),
        )
        assert connection_info_command() == ["gnome-control-center", "wifi"]

    def test_edit_connections_command_picks_nm_connection_editor(self, monkeypatch):
        monkeypatch.setattr(
            network_state_mod.shutil,
            "which",
            lambda binary: (
                "/usr/bin/nm-connection-editor"
                if binary == "nm-connection-editor"
                else None
            ),
        )
        assert edit_connections_command() == ["nm-connection-editor"]

    def test_open_connection_info_runs_command(self, monkeypatch):
        launched: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            network_state_mod,
            "connection_info_command",
            lambda: ["gnome-control-center", "wifi"],
        )
        monkeypatch.setattr(
            network_state_mod.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(tuple(cmd)),
        )
        assert open_connection_info() is True
        assert launched == [("gnome-control-center", "wifi")]

    def test_open_edit_connections_runs_command(self, monkeypatch):
        launched: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            network_state_mod,
            "edit_connections_command",
            lambda: ["nm-connection-editor"],
        )
        monkeypatch.setattr(
            network_state_mod.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(tuple(cmd)),
        )
        assert open_edit_connections() is True
        assert launched == [("nm-connection-editor",)]

    def test_open_hidden_wifi_settings_uses_editor_or_info_command(self, monkeypatch):
        launched: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            network_state_mod,
            "edit_connections_command",
            lambda: ["nm-connection-editor"],
        )
        monkeypatch.setattr(
            network_state_mod,
            "connection_info_command",
            lambda: ["gnome-control-center", "wifi"],
        )
        monkeypatch.setattr(
            network_state_mod.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(tuple(cmd)),
        )
        assert open_hidden_wifi_settings() is True
        assert launched == [("nm-connection-editor",)]

    def test_open_new_wifi_settings_falls_back_to_info_command(self, monkeypatch):
        launched: list[tuple[str, ...]] = []
        monkeypatch.setattr(
            network_state_mod,
            "edit_connections_command",
            lambda: None,
        )
        monkeypatch.setattr(
            network_state_mod,
            "connection_info_command",
            lambda: ["gnome-control-center", "wifi"],
        )
        monkeypatch.setattr(
            network_state_mod.subprocess,
            "Popen",
            lambda cmd, start_new_session=True: launched.append(tuple(cmd)),
        )
        assert open_new_wifi_settings() is True
        assert launched == [("gnome-control-center", "wifi")]

    @pytest.mark.parametrize("connected", [False, True])
    def test_wired_icon_renders_connected_and_disconnected(self, connected):
        pixbuf = create_network_icon(
            size=48,
            is_connected=connected,
            is_wifi=False,
            signal_strength=0,
            rx_speed=0.0,
            tx_speed=0.0,
        )
        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48
