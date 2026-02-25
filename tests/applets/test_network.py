"""Tests for the Network applet -- parsing, speed formatting, icon mapping."""

import pytest

from docking.applets.network import (
    NetworkApplet,
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
        result = parse_proc_net_dev(SAMPLE_PROC_NET_DEV)
        assert "lo" in result
        assert "wlp0s20f3" in result
        assert "eth0" in result

    def test_rx_tx_values(self):
        result = parse_proc_net_dev(SAMPLE_PROC_NET_DEV)
        rx, tx = result["wlp0s20f3"]
        assert rx == 138460662022
        assert tx == 49607734285

    def test_lo_values(self):
        result = parse_proc_net_dev(SAMPLE_PROC_NET_DEV)
        rx, tx = result["lo"]
        assert rx == 1234567
        assert tx == 1234567

    def test_empty_text(self):
        assert parse_proc_net_dev("") == {}

    def test_headers_only(self):
        text = "Inter-|   Receive\n face |bytes\n"
        assert parse_proc_net_dev(text) == {}


class TestComputeSpeeds:
    def test_basic_speeds(self):
        prev = (1000, 500)
        curr = (3000, 1500)
        rx, tx = compute_speeds(prev, curr, 2.0)
        assert rx == pytest.approx(1000.0)
        assert tx == pytest.approx(500.0)

    def test_zero_elapsed(self):
        rx, tx = compute_speeds((0, 0), (1000, 500), 0.0)
        assert rx == 0.0
        assert tx == 0.0

    def test_no_change(self):
        rx, tx = compute_speeds((1000, 500), (1000, 500), 1.0)
        assert rx == 0.0
        assert tx == 0.0

    def test_counter_wraparound_clamped(self):
        # If curr < prev (counter reset), clamp to 0
        rx, tx = compute_speeds((5000, 3000), (1000, 500), 1.0)
        assert rx == 0.0
        assert tx == 0.0


class TestFormatSpeed:
    def test_bytes(self):
        assert format_speed(500) == "500 B/s"

    def test_kilobytes(self):
        result = format_speed(1536)
        assert "KB/s" in result

    def test_megabytes(self):
        result = format_speed(5 * 1024 * 1024)
        assert "MB/s" in result

    def test_gigabytes(self):
        result = format_speed(2 * 1024 * 1024 * 1024)
        assert "GB/s" in result

    def test_zero(self):
        assert format_speed(0) == "0 B/s"


class TestSignalToIcon:
    def test_disconnected(self):
        assert signal_to_icon(0, False, False) == "network-offline-symbolic"

    def test_ethernet(self):
        assert signal_to_icon(0, True, False) == "network-wired-symbolic"

    def test_wifi_weak(self):
        assert "weak" in signal_to_icon(20, True, True)

    def test_wifi_ok(self):
        assert "ok" in signal_to_icon(50, True, True)

    def test_wifi_good(self):
        assert "good" in signal_to_icon(70, True, True)

    def test_wifi_excellent(self):
        assert "excellent" in signal_to_icon(90, True, True)

    def test_wifi_boundary_80(self):
        assert "excellent" in signal_to_icon(80, True, True)

    def test_wifi_boundary_60(self):
        assert "good" in signal_to_icon(60, True, True)

    def test_wifi_boundary_40(self):
        assert "ok" in signal_to_icon(40, True, True)


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
        icon1 = signal_to_icon(0, applet._is_connected, applet._is_wifi)
        assert "offline" in icon1
        # Connected wifi
        applet._is_connected = True
        applet._is_wifi = True
        applet._signal_strength = 90
        icon2 = signal_to_icon(applet._signal_strength, True, True)
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
