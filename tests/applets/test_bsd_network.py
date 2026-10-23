"""BSD network sampling, interface changes, and applet platform availability."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.applets.network.applet as network_applet
from docking.applets.bluetooth import meta as bluetooth_meta
from docking.applets.network.applet import NetworkApplet
from docking.core.config import Config
from docking.platform import bsd, environment


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


def test_linux_only_applet_is_explicitly_unavailable_on_bsd(monkeypatch):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    assert not bluetooth_meta.available_on_host
    monkeypatch.setattr(environment, "is_bsd", lambda: False)
    assert bluetooth_meta.available_on_host


def test_camshield_cannot_invoke_linux_helper_from_a_saved_bsd_pin(monkeypatch):
    from docking.applets.camshield.applet import _helper_command

    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    assert _helper_command(action="lock") is None


def test_shared_applet_starts_without_networkmanager_and_keeps_preferences(monkeypatch):
    monkeypatch.setattr(environment, "is_bsd", lambda: True)
    monkeypatch.setattr(
        bsd,
        "network_interfaces",
        lambda: [bsd.NetworkInterface("vio0", "192.0.2.1", True, 1000, 2000)],
    )
    new_client = MagicMock(side_effect=AssertionError("BSD must not load NM"))
    monkeypatch.setattr(
        network_applet,
        "NM",
        SimpleNamespace(Client=SimpleNamespace(new=new_client)),
        raising=False,
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
        new_client.assert_not_called()
    finally:
        applet.stop()
    assert applet._timer_id == 0
