"""BSD network sampling, interface changes, and applet platform availability."""

import socket
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.applets.bluetooth import meta as bluetooth_meta
from docking.applets.network import bsd
from docking.applets.network.applet import NetworkApplet
from docking.core.config import Config
from docking.platform import environment


def test_native_interface_change_resets_traffic_baseline(monkeypatch):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    readings = [bsd.NetworkInterface("vio0", "192.0.2.1", True, 1000, 2000)]
    monkeypatch.setattr(bsd, "network_interfaces", lambda: readings)
    applet = NetworkApplet(48, Config())
    applet._tick()
    assert applet._state.iface == "vio0"
    assert applet._state.rx_speed == 0
    readings[:] = [bsd.NetworkInterface("em0", "192.0.2.2", True, 900000, 900000)]
    applet._tick()
    assert applet._state.iface == "em0"
    assert applet._state.rx_speed == 0
    readings.clear()
    applet._tick()
    assert not applet._state.is_connected
    assert applet._prev_counters is None


def test_bluetooth_available_on_freebsd_but_not_openbsd(monkeypatch):
    from docking.applets import identity
    from docking.applets.camshield import meta as camera_meta

    monkeypatch.setattr(identity.sys, "platform", "freebsd15")
    assert bluetooth_meta.available_on_host
    assert camera_meta.available_on_host
    monkeypatch.setattr(identity.sys, "platform", "openbsd7")
    assert not bluetooth_meta.available_on_host
    assert camera_meta.available_on_host


def test_shared_applet_starts_without_networkmanager_and_keeps_preferences(monkeypatch):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    monkeypatch.setattr(
        bsd,
        "network_interfaces",
        lambda: [bsd.NetworkInterface("vio0", "192.0.2.1", True, 1000, 2000)],
    )
    applet = NetworkApplet(48, Config())
    applet.save_prefs = MagicMock()
    try:
        applet.start(notify=lambda: None)
        assert applet._state.is_connected
        assert applet._timer_id
        assert applet.get_menu_items()
        applet._set_interface("vio0")
        applet._set_speed_overlay("upload")
        applet.save_prefs.assert_called_with(
            prefs={"interface": "vio0", "speed_overlay": "upload"}
        )
        assert applet._backend.connection_manager is None
    finally:
        applet.stop()
    assert applet._timer_id == 0


def test_network_uses_native_counters_and_ignores_loopback(monkeypatch):
    counters = SimpleNamespace(bytes_recv=1000, bytes_sent=2000)
    util = SimpleNamespace(
        net_io_counters=lambda **kwargs: {"lo0": counters, "vio0": counters},
        net_if_addrs=lambda: {
            "vio0": [SimpleNamespace(family=socket.AF_INET, address="192.0.2.5")]
        },
        net_if_stats=lambda: {
            "lo0": SimpleNamespace(isup=True),
            "vio0": SimpleNamespace(isup=True),
        },
    )
    monkeypatch.setattr(bsd, "_psutil", lambda: util)
    assert bsd.network_interfaces() == [
        bsd.NetworkInterface("vio0", "192.0.2.5", True, 1000, 2000)
    ]


@pytest.mark.parametrize(
    "addresses,expected",
    [
        ([(socket.AF_INET6, "2001:db8::5")], "2001:db8::5"),
        (
            [(socket.AF_INET6, "2001:db8::5"), (socket.AF_INET, "192.0.2.5")],
            "192.0.2.5",
        ),
        ([], ""),
    ],
    ids=("ipv6-only", "dual-stack", "no-address"),
)
def test_network_connection_accepts_both_ip_families(monkeypatch, addresses, expected):
    counter = SimpleNamespace(bytes_recv=1000, bytes_sent=2000)
    util = SimpleNamespace(
        net_io_counters=lambda **kwargs: {"vio0": counter},
        net_if_addrs=lambda: {
            "vio0": [
                SimpleNamespace(family=family, address=address)
                for family, address in addresses
            ]
        },
        net_if_stats=lambda: {"vio0": SimpleNamespace(isup=True)},
    )
    monkeypatch.setattr(bsd, "_psutil", lambda: util)
    state = bsd.BsdNetworkBackend().state()
    assert state.ip_address == expected
    assert state.is_connected is bool(expected)
