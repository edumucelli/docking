"""BSD network sampling, interface changes, and applet platform availability."""

from docking.applets.bluetooth import meta as bluetooth_meta
from docking.applets.network.bsd_applet import NetworkApplet
from docking.core.config import Config
from docking.platform import bsd


def test_native_interface_change_resets_traffic_baseline(monkeypatch):
    readings = [bsd.NetworkInterface("vio0", "192.0.2.1", True, 1000, 2000)]
    monkeypatch.setattr(bsd, "network_interfaces", lambda: readings)
    applet = NetworkApplet(48, Config())
    assert applet._state.iface == "vio0"
    assert applet._state.rx_speed == 0
    readings[:] = [bsd.NetworkInterface("em0", "192.0.2.2", True, 900000, 900000)]
    applet._tick()
    assert applet._state.iface == "em0"
    assert applet._state.rx_speed == 0
    readings.clear()
    applet._tick()
    assert not applet._state.is_connected
    assert applet._previous is None


def test_linux_only_applet_is_explicitly_unavailable_on_bsd(monkeypatch):
    monkeypatch.setattr(bsd, "is_bsd", lambda: True)
    assert not bluetooth_meta.available_on_host
    monkeypatch.setattr(bsd, "is_bsd", lambda: False)
    assert bluetooth_meta.available_on_host
