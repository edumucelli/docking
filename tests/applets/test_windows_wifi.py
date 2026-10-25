"""WLAN ABI ownership, profile identity and shared menu capability regressions."""

import ctypes
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.network import windows_profiles as profiles
from docking.applets.network import windows_wifi as wifi
from docking.applets.network.applet import NetworkApplet
from docking.applets.network.backend import NetworkCapabilities
from docking.applets.network.render import create_icon
from docking.applets.network.state import NetworkState, build_tooltip
from docking.platform.backends.windows.com import GUID

ID = "{9cd793bb-5594-4e16-b57a-e8a43f0a4b5f}"
OTHER = "{7f85160a-5500-4188-aa78-751926ed48fe}"


def _candidate(**values):
    return wifi._Candidate(
        **{
            "identifier": ID,
            "ssid": b"Cafe",
            "profile": "Saved profile",
            "bss_type": 1,
            "strength": 80,
            "active": False,
            "connectable": True,
            **values,
        }
    )


@pytest.fixture
def native(monkeypatch):
    names = (
        "WlanOpenHandle",
        "WlanCloseHandle",
        "WlanEnumInterfaces",
        "WlanQueryInterface",
        "WlanSetInterface",
        "WlanScan",
        "WlanGetAvailableNetworkList",
        "WlanConnect",
        "WlanFreeMemory",
    )
    dll = SimpleNamespace(**{name: Mock(return_value=0) for name in names})
    monkeypatch.setattr(wifi.ctypes, "WinDLL", lambda *_a, **_kw: dll, raising=False)
    memory = []

    def output(pointer, value):
        memory.append(value)
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(
            value
        )

    def open_handle(version, reserved, negotiated, handle):
        assert version == 2 and reserved is None
        ctypes.cast(negotiated, ctypes.POINTER(ctypes.c_uint32))[0] = 2
        ctypes.cast(handle, ctypes.POINTER(ctypes.c_void_p))[0] = 1234
        return 0

    def interfaces(_handle, _reserved, pointer):
        value = wifi._Interfaces(count=1)
        value.items[0].identifier = GUID.parse(ID)
        output(pointer, value)
        return 0

    dll.WlanOpenHandle.side_effect = open_handle
    dll.WlanEnumInterfaces.side_effect = interfaces
    return wifi._Wlan(), dll, output


def test_wlan_abi_uses_windows_widths_on_any_host():
    assert ctypes.sizeof(wifi._Interface) == 532
    assert wifi._Interfaces.items.offset == wifi._Networks.items.offset == 8
    assert ctypes.sizeof(wifi._SSID) == 36
    assert ctypes.sizeof(wifi._Network) == 628
    assert wifi._Network.strength.offset == 604
    assert ctypes.sizeof(wifi._Radio) == 772
    assert ctypes.sizeof(wifi._PhyRadio) == 12
    assert ctypes.sizeof(wifi._ConnectionParameters) == (
        40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24
    )


def test_radio_snapshot_frees_each_allocation_and_closes_client(native):
    api, dll, output = native

    def radio(handle, guid, opcode, reserved, size, pointer, value_type):
        assert (
            handle.value == 1234
            and opcode == 4
            and reserved is None
            and value_type is None
        )
        assert bytes(ctypes.cast(guid, ctypes.POINTER(GUID)).contents) == bytes(
            GUID.parse(ID)
        )
        value = wifi._Radio(count=2)
        value.phys[0] = wifi._PhyRadio(0, 1, 1)
        value.phys[1] = wifi._PhyRadio(3, 2, 1)
        ctypes.cast(size, ctypes.POINTER(ctypes.c_uint32))[0] = ctypes.sizeof(value)
        output(pointer, value)
        return 0

    dll.WlanQueryInterface.side_effect = radio
    identifiers, radios = api.radios()
    assert identifiers == [ID]
    assert radios == [
        wifi._RadioState(ID, 0, True, True),
        wifi._RadioState(ID, 3, False, True),
    ]
    assert dll.WlanFreeMemory.call_count == 2
    dll.WlanCloseHandle.assert_called_once()


@pytest.mark.parametrize("count,size", [(65, 772), (2, 16), (1, 0)])
def test_truncated_radio_state_does_not_read_past_buffer(native, count, size):
    api, dll, output = native

    def radio(_h, _g, _op, _r, length, pointer, _t):
        value = wifi._Radio(count=count)
        ctypes.cast(length, ctypes.POINTER(ctypes.c_uint32))[0] = size
        output(pointer, value)
        return 0

    dll.WlanQueryInterface.side_effect = radio
    assert api.radios() == ([ID], [])
    assert dll.WlanFreeMemory.call_count == 2
    dll.WlanCloseHandle.assert_called_once()


def test_enumeration_failure_still_releases_allocations_and_handle(native):
    api, dll, output = native

    def failed(_h, _r, pointer):
        output(pointer, wifi._Interfaces(count=0))
        return 5

    dll.WlanEnumInterfaces.side_effect = failed
    with pytest.raises(OSError) as error:
        api.radios()
    assert error.value.errno == 5
    dll.WlanFreeMemory.assert_called_once()
    dll.WlanCloseHandle.assert_called_once()


def test_stopped_service_does_not_close_an_unacquired_handle(native):
    api, dll, _output = native
    dll.WlanOpenHandle.side_effect = None
    dll.WlanOpenHandle.return_value = 1062
    with pytest.raises(OSError) as error:
        api.radios()
    assert error.value.errno == 1062
    dll.WlanCloseHandle.assert_not_called()


def test_visible_list_keeps_raw_ssid_unicode_profile_and_connectability(native):
    api, dll, output = native

    def networks(_h, _g, flags, reserved, pointer):
        assert flags == 0 and reserved is None
        value = wifi._Networks(count=1)
        entry = value.items[0]
        raw = b" Cafe\xff "
        entry.ssid = wifi._SSID(len(raw), (ctypes.c_ubyte * 32)(*raw))
        name = "Profil 🛜"
        encoded = name.encode("utf-16-le")
        ctypes.memmove(entry.profile, encoded, len(encoded))
        entry.strength, entry.flags, entry.connectable, entry.bss_type = 60, 3, 0, 1
        output(pointer, value)
        return 0

    dll.WlanGetAvailableNetworkList.side_effect = networks
    values = api.available(scan=True)
    assert values == [
        _candidate(
            ssid=b" Cafe\xff ",
            profile="Profil 🛜",
            strength=60,
            active=True,
            connectable=False,
        )
    ]
    assert values[0].visible().ssid == " Cafe\ufffd "
    assert not values[0].visible().is_connectable
    dll.WlanScan.assert_called_once()
    assert dll.WlanFreeMemory.call_count == 2
    dll.WlanCloseHandle.assert_called_once()


def test_permission_denied_scan_never_reads_or_fabricates_networks(native):
    api, dll, _output = native
    dll.WlanScan.return_value = 5
    with pytest.raises(OSError) as error:
        api.available(scan=True)
    assert error.value.errno == 5
    dll.WlanGetAvailableNetworkList.assert_not_called()
    dll.WlanCloseHandle.assert_called_once()


def test_connect_uses_exact_profile_raw_ssid_and_no_plaintext_credentials(native):
    api, dll, _output = native
    value = _candidate(ssid=b"\xffCafe ", profile="Office 🛜")

    def connect(_h, guid, parameters, reserved):
        assert reserved is None
        assert bytes(ctypes.cast(guid, ctypes.POINTER(GUID)).contents) == bytes(
            GUID.parse(ID)
        )
        request = ctypes.cast(
            parameters, ctypes.POINTER(wifi._ConnectionParameters)
        ).contents
        assert request.mode == 0 and request.flags == 0 and request.bss_type == 1
        assert not request.bssids
        assert (
            ctypes.string_at(
                request.profile, len(value.profile.encode("utf-16-le"))
            ).decode("utf-16-le")
            == value.profile
        )
        assert (
            bytes(request.ssid.contents.data[: request.ssid.contents.length])
            == value.ssid
        )
        return 0

    dll.WlanConnect.side_effect = connect
    api.connect(value)
    dll.WlanCloseHandle.assert_called_once()


def test_radio_write_uses_fresh_phy_indices_and_software_state(native, monkeypatch):
    api, dll, _output = native
    monkeypatch.setattr(
        api, "radios", lambda: ([ID], [wifi._RadioState(ID, 3, False, False)])
    )

    def write(_h, _g, opcode, size, pointer, reserved):
        value = ctypes.cast(pointer, ctypes.POINTER(wifi._PhyRadio)).contents
        assert (
            opcode,
            size,
            value.index,
            value.software,
            value.hardware,
            reserved,
        ) == (4, 12, 3, 1, 0, None)
        return 0

    dll.WlanSetInterface.side_effect = write
    api.set_radio(enabled=True)
    dll.WlanCloseHandle.assert_called_once()


def _manager(values=()):
    api = SimpleNamespace(
        radios=Mock(return_value=([ID], [wifi._RadioState(ID, 0, True, True)])),
        available=Mock(return_value=list(values)),
        connect=Mock(),
        set_radio=Mock(),
    )
    manager = wifi.WindowsWifiManager(api=api)
    manager.refresh()
    return manager, api


def test_manager_uses_capabilities_and_actual_hardware_state():
    manager, api = _manager()
    assert manager.has_wifi_device() and manager.wireless_enabled
    assert manager.capabilities == NetworkCapabilities(False, True)
    api.radios.return_value = ([ID], [wifi._RadioState(ID, 0, True, False)])
    manager.refresh()
    assert manager.wireless_enabled and not manager.wireless_hardware_enabled
    api.radios.side_effect = OSError(1062, "service stopped")
    manager.refresh()
    assert not manager.has_wifi_device() and not manager.capabilities.wireless_toggle
    with pytest.raises(NotImplementedError):
        manager.set_networking_enabled(enabled=False)


def test_discovery_is_user_driven_throttled_and_reports_denial_then_recovers(
    monkeypatch,
):
    manager, api = _manager([_candidate()])
    now = [100.0]
    monkeypatch.setattr(wifi.time, "monotonic", lambda: now[0])
    api.available.assert_not_called()
    assert len(manager.available_networks()) == 1
    api.available.assert_called_with(scan=True)
    manager.available_networks()
    api.available.assert_called_with(scan=False)
    now[0] += 16
    api.available.side_effect = OSError(5, "denied")
    assert manager.available_networks() == []
    assert manager.networks_unavailable_reason == "Permission denied"
    api.available.side_effect = None
    assert manager.available_networks()
    assert not manager.networks_unavailable_reason


@pytest.mark.parametrize("change", ["adapter", "profile", "raw_ssid", "connectable"])
def test_old_menu_never_connects_a_different_or_unavailable_candidate(change):
    original = _candidate()
    manager, api = _manager([original])
    network = manager.available_networks()[0]
    changes = {
        "adapter": {"identifier": OTHER},
        "profile": {"profile": "Other"},
        "raw_ssid": {"ssid": b"Other"},
        "connectable": {"connectable": False},
    }
    api.available.return_value = [replace(original, **changes[change])]
    manager.connect_network(network=network)
    api.connect.assert_not_called()


def test_profile_connection_is_revalidated_and_refreshes_state():
    value = _candidate()
    manager, api = _manager([value])
    manager.changed = Mock()
    network = manager.available_networks()[0]
    manager.connect_network(network=network)
    api.connect.assert_called_once_with(value)
    manager.changed.assert_called_once()
    manager.stop()
    manager.connect_network(network=network)
    assert api.connect.call_count == 1


def test_new_network_uses_native_credentials_ui(monkeypatch):
    from docking.platform.backends.windows import shell

    manager, api = _manager([_candidate(profile="")])
    launch = Mock(return_value=True)
    monkeypatch.setattr(shell, "open_default_target", launch)
    manager.connect_network(network=manager.available_networks()[0])
    api.connect.assert_not_called()
    launch.assert_called_once_with("ms-settings:network-wifi")


def test_radio_error_does_not_claim_success_and_reloads_observed_state():
    manager, api = _manager()
    manager.changed = Mock()
    api.set_radio.side_effect = OSError(5, "denied")
    manager.set_wireless_enabled(enabled=False)
    assert manager.wireless_enabled
    assert api.radios.call_count == 2
    manager.changed.assert_called_once()


def test_unknown_signal_has_distinct_artwork_and_no_fake_percent():
    text = build_tooltip(True, "Cafe", None, "Wi-Fi", "192.0.2.2", 0, 0, True)
    assert "Cafe" in text and "%" not in text and "None" not in text
    unknown = create_icon(48, True, True, None, 0, 0).get_pixels()
    assert unknown != create_icon(48, True, True, 0, 0, 0).get_pixels()
    assert unknown != create_icon(48, False, True, 0, 0, 0).get_pixels()


def test_shared_menu_hides_unsupported_global_toggle_and_shows_denial(monkeypatch):
    import docking.applets.network.applet as applet_module
    from docking.applets.network.windows import WindowsNetworkBackend
    from docking.core.config import Config

    manager, api = _manager()
    api.available.side_effect = OSError(5, "denied")
    backend = WindowsNetworkBackend(wifi=manager)
    monkeypatch.setattr(applet_module, "create_backend", lambda _interface: backend)
    applet = NetworkApplet(48, Config())
    applet._state = NetworkState(True, True, "Cafe", None, "Wi-Fi")
    items = applet.get_menu_items()
    labels = [item.get_label() for item in items]
    assert "Enable Networking" not in labels and "Enable Wi-Fi" in labels
    subnet = next(
        item.get_submenu() for item in items if item.get_label() == "Available Networks"
    )
    assert [item.get_label() for item in subnet.get_children()] == ["Permission denied"]
    assert not subnet.get_children()[0].get_sensitive()


@pytest.mark.skipif(
    sys.platform != "win32", reason="Native Windows connection profiles"
)
def test_native_profiles_match_ip_helper_ids_and_release_handles():
    from docking.applets.network.windows import _IPHelper

    known = {a.identifier for a in _IPHelper().adapters()}
    for _ in range(4):
        assert all(p.identifier in known for p in profiles.read_connections())


def test_duplicate_ssids_prefer_a_usable_adapter_over_a_blocked_stronger_one():
    first = _candidate(strength=100, connectable=False)
    second = _candidate(identifier=OTHER, strength=60)
    manager, api = _manager([first, second])
    values = manager.available_networks()
    assert len(values) == 1 and values[0].is_connectable
    manager.connect_network(network=values[0])
    api.connect.assert_called_once_with(second)


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows WinRT apartments")
def test_native_profile_queries_support_existing_sta_and_concurrent_mta():
    from concurrent.futures import ThreadPoolExecutor

    from docking.platform.backends.windows.com import apartment

    with apartment():
        expected = profiles.read_connections()
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(
            executor.map(lambda _index: profiles.read_connections(), range(8))
        )
    assert all(v == expected for v in values)
