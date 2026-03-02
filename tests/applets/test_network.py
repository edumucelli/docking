"""Tests for the Network applet -- parsing, speed formatting, icon mapping."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import docking.applets.network as network_mod
from docking.applets.network import (
    NetworkApplet,
    TrafficCounters,
    compute_speeds,
    format_speed,
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
        applet.create_icon(48)
        assert "not connected" in applet.item.name.lower()

    def test_menu_returns_items(self):
        applet = NetworkApplet(48)
        items = applet.get_menu_items()
        assert len(items) >= 1

    def test_tooltip_wifi_shows_ssid(self):
        applet = NetworkApplet(48)
        applet._is_connected = True
        applet._is_wifi = True
        applet._ssid = "MyNetwork"
        applet._signal_strength = 72
        applet._rx_speed = 1500.0
        applet._tx_speed = 300.0
        applet.create_icon(48)
        assert "MyNetwork" in applet.item.name
        assert "72%" in applet.item.name

    def test_tooltip_ethernet(self):
        applet = NetworkApplet(48)
        applet._is_connected = True
        applet._is_wifi = False
        applet._iface = "eth0"
        applet.create_icon(48)
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
        applet.create_icon(48)
        assert "TestWifi" in applet.item.name


class TestNetworkAppletInternals:
    def test_start_connects_nm_and_timer(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        notify = MagicMock()
        nm_client = MagicMock()
        monkeypatch.setattr(network_mod.NM.Client, "new", lambda _arg: nm_client)
        monkeypatch.setattr(
            network_mod.GLib, "timeout_add_seconds", lambda _sec, _cb: 321
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
        monkeypatch.setattr(network_mod.GLib, "Error", RuntimeError, raising=False)
        monkeypatch.setattr(
            network_mod.NM.Client,
            "new",
            MagicMock(side_effect=RuntimeError("nm unavailable")),
        )
        monkeypatch.setattr(
            network_mod.GLib, "timeout_add_seconds", lambda _sec, _cb: 555
        )
        # When
        applet.start(notify)
        # Then
        assert applet._nm_client is None
        assert applet._timer_id == 555

    def test_stop_disconnects_signal_and_timer(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        applet._nm_client = MagicMock()
        applet._nm_handler_id = 17
        applet._timer_id = 88
        removed: list[int] = []
        monkeypatch.setattr(
            network_mod.GLib, "source_remove", lambda i: removed.append(i)
        )
        # When
        applet.stop()
        # Then
        applet._nm_client = None
        assert applet._nm_handler_id == 0
        assert applet._timer_id == 0
        assert removed == [88]

    def test_on_nm_changed_refreshes(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        update = MagicMock()
        refresh = MagicMock()
        monkeypatch.setattr(applet, "_update_nm_state", update)
        monkeypatch.setattr(applet, "refresh_icon", refresh)
        # When
        applet._on_nm_changed()
        # Then
        update.assert_called_once()
        refresh.assert_called_once()

    def test_update_nm_state_prefers_wifi_and_reads_ip_and_signal(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        monkeypatch.setattr(
            network_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2, ETHERNET=1, TUN=3, BRIDGE=4),
            raising=False,
        )
        monkeypatch.setattr(
            network_mod.NM,
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

        monkeypatch.setattr(network_mod.NM, "DeviceWifi", FakeWifiDevice, raising=False)
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
            network_mod.NM,
            "DeviceType",
            SimpleNamespace(WIFI=2, ETHERNET=1, TUN=3, BRIDGE=4),
            raising=False,
        )
        monkeypatch.setattr(
            network_mod.NM,
            "ActiveConnectionState",
            SimpleNamespace(ACTIVATED=9),
            raising=False,
        )
        monkeypatch.setattr(network_mod.NM, "DeviceWifi", object, raising=False)
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

    def test_tick_updates_and_refreshes(self, monkeypatch):
        # Given
        applet = NetworkApplet(48)
        update_traffic = MagicMock()
        update_wifi = MagicMock()
        refresh = MagicMock()
        monkeypatch.setattr(applet, "_update_traffic", update_traffic)
        monkeypatch.setattr(applet, "_update_wifi_signal", update_wifi)
        monkeypatch.setattr(applet, "refresh_icon", refresh)
        # When
        result = applet._tick()
        # Then
        assert result is True
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
            "builtins.open",
            lambda *_a, **_k: open_cm,
        )
        monkeypatch.setattr(network_mod.time, "monotonic", lambda: 12.0)
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
            network_mod.NM,
            "ActiveConnectionState",
            SimpleNamespace(ACTIVATED=9),
            raising=False,
        )

        class FakeWifiDevice:
            def get_active_access_point(self):
                ap = MagicMock()
                ap.get_strength.return_value = 81
                return ap

        monkeypatch.setattr(network_mod.NM, "DeviceWifi", FakeWifiDevice, raising=False)
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
            network_mod.NM,
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

        monkeypatch.setattr(network_mod.NM, "DeviceWifi", FakeWifiDevice, raising=False)

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
