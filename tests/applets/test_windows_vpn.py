"""RAS ABI, stable profile identity, native dialer and disconnect lifecycle."""

import ctypes
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.network import windows_ras as ras
from docking.applets.network.backend import VpnConnection
from docking.applets.network.windows_vpn import WindowsVpn
from docking.platform.backends.windows.com import GUID

GUID1 = "965b6853-5983-4ced-92ba-897b18259f2e"
GUID2 = "d97e360b-1291-443e-b526-2baee218ebc5"
BOOK = r"C:\Users\Test\résumé folder\rasphone.pbk"


def _profile(**values):
    return ras.VpnProfile(
        **{"name": "Café VPN", "phonebook": BOOK, "identifier": GUID1, **values}
    )


def _session(profile=None, **values):
    p = profile or _profile()
    return ras.VpnSession(
        **{
            "name": p.name,
            "phonebook": p.phonebook,
            "identifier": p.identifier,
            "handle": 501,
            "connected": True,
            **values,
        }
    )


def _write_text(array, text):
    raw = (text + "\0").encode("utf-16-le")
    ctypes.memmove(array, raw, len(raw))


@pytest.fixture
def native(monkeypatch):
    names = (
        "RasEnumEntriesW",
        "RasEnumConnectionsW",
        "RasGetEntryPropertiesW",
        "RasGetConnectStatusW",
        "RasHangUpW",
    )
    dll = SimpleNamespace(**{n: Mock(return_value=0) for n in names})
    monkeypatch.setattr(ras.ctypes, "WinDLL", lambda *_a, **_kw: dll, raising=False)
    return ras._Ras(), dll


def test_ras_abi_has_sdk_pack_four_even_with_64bit_handles():
    assert ctypes.sizeof(ras._EntryName) == 1048
    assert ctypes.sizeof(ras._Entry) == 6724
    assert ras._Entry.kind.offset == 3496 and ras._Entry.identifier.offset == 3508
    assert ras._Connection.handle.offset == 4
    assert ctypes.sizeof(ras._Connection) == (
        1388 if ctypes.sizeof(ctypes.c_void_p) == 8 else 1384
    )
    assert ctypes.sizeof(ras._Status) == 608


def test_entries_resize_and_use_exact_phonebook_type_and_guid(native):
    api, dll = native
    rows = []
    for index in range(2):
        entry = ras._EntryName(size=ctypes.sizeof(ras._EntryName))
        _write_text(entry.name, "Café 🛜 VPN")
        _write_text(
            entry.phonebook, BOOK if index == 0 else r"C:\ProgramData\rasphone.pbk"
        )
        entry.flags = index
        rows.append(entry)

    def entries(reserved, book, buffer, size, count):
        assert reserved is None and book is None
        assert ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint32))[0] == ctypes.sizeof(
            ras._EntryName
        )
        size_ptr = ctypes.cast(size, ctypes.POINTER(ctypes.c_uint32))
        if size_ptr[0] < len(rows) * ctypes.sizeof(ras._EntryName):
            size_ptr[0] = len(rows) * ctypes.sizeof(ras._EntryName)
            return 603
        for i, row in enumerate(rows):
            ctypes.memmove(
                ctypes.addressof(buffer) + i * ctypes.sizeof(row),
                ctypes.byref(row),
                ctypes.sizeof(row),
            )
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 2
        return 0

    def properties(book, name, buffer, size, device, device_size):
        assert name == "Café 🛜 VPN" and device is None and device_size is None
        value = ras._Entry(
            size=ctypes.sizeof(ras._Entry),
            kind=2,
            identifier=GUID.parse(GUID1 if book == BOOK else GUID2),
        )
        ctypes.memmove(buffer, ctypes.byref(value), ctypes.sizeof(value))
        return 0

    dll.RasEnumEntriesW.side_effect = entries
    dll.RasGetEntryPropertiesW.side_effect = properties
    values = api.profiles()
    assert len(values) == 2 and values[0].key != values[1].key
    assert values[0].identifier == GUID1 and values[1].all_users
    assert dll.RasEnumEntriesW.call_count == 2


def test_enumeration_rejects_success_count_larger_than_allocation(native):
    api, dll = native

    def entries(_r, _b, _buffer, _size, count):
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 2
        return 0

    dll.RasEnumEntriesW.side_effect = entries
    with pytest.raises(OSError, match="exceeds"):
        api.profiles()


def test_repeated_topology_growth_is_bounded(native):
    api, dll = native

    def entries(_r, _b, _buffer, size, _count):
        ctypes.cast(size, ctypes.POINTER(ctypes.c_uint32))[0] += 1048
        return 603

    dll.RasEnumEntriesW.side_effect = entries
    with pytest.raises(OSError):
        api.profiles()
    assert dll.RasEnumEntriesW.call_count == 4


def test_non_vpn_and_disappearing_entries_are_not_actions(native):
    api, dll = native

    def entries(_r, _b, buffer, _size, count):
        entry = ras._EntryName(size=1048)
        _write_text(entry.name, "modem")
        _write_text(entry.phonebook, BOOK)
        ctypes.memmove(buffer, ctypes.byref(entry), 1048)
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 1
        return 0

    dll.RasEnumEntriesW.side_effect = entries
    dll.RasGetEntryPropertiesW.return_value = 623
    assert api.profiles() == []
    dll.RasGetEntryPropertiesW.return_value = 0
    assert api.profiles() == []


@pytest.mark.parametrize("gone", [6, 1168])
def test_session_active_state_requires_connect_status_not_handle_presence(native, gone):
    api, dll = native

    def connections(buffer, _size, count):
        entry = ras._Connection(
            size=ctypes.sizeof(ras._Connection),
            handle=501,
            identifier=GUID.parse(GUID1),
            flags=4,
        )
        _write_text(entry.name, "Café VPN")
        _write_text(entry.phonebook, BOOK)
        _write_text(entry.device_type, "vpn")
        ctypes.memmove(buffer, ctypes.byref(entry), ctypes.sizeof(entry))
        ctypes.cast(count, ctypes.POINTER(ctypes.c_uint32))[0] = 1
        return 0

    def status(handle, pointer):
        assert handle == 501
        value = ctypes.cast(pointer, ctypes.POINTER(ras._Status)).contents
        assert value.size == 608
        value.state = 0x2000
        value.error = 0
        return 0

    dll.RasEnumConnectionsW.side_effect = connections
    dll.RasGetConnectStatusW.side_effect = status
    assert api.sessions() == [_session(can_disconnect=False)]
    dll.RasGetConnectStatusW.side_effect = None
    dll.RasGetConnectStatusW.return_value = gone
    assert api.sessions() == []


@pytest.mark.parametrize("gone", [6, 1168])
def test_disconnect_revalidates_handle_identity_and_waits_for_release(
    native, monkeypatch, gone
):
    api, dll = native
    session = _session()
    monkeypatch.setattr(api, "sessions", lambda: [session])
    dll.RasGetConnectStatusW.side_effect = [0, gone]
    monkeypatch.setattr(ras.time, "sleep", lambda _seconds: None)
    api.disconnect([session])
    dll.RasHangUpW.assert_called_once_with(501)
    assert dll.RasGetConnectStatusW.call_count == 2


@pytest.mark.parametrize(
    "change", [{"identifier": GUID2}, {"handle": 502}, {"can_disconnect": False}]
)
def test_disconnect_does_not_touch_reused_or_other_owner_handles(
    native, monkeypatch, change
):
    api, dll = native
    session = _session()
    monkeypatch.setattr(api, "sessions", lambda: [replace(session, **change)])
    api.disconnect([session])
    dll.RasHangUpW.assert_not_called()


def test_dialer_uses_system_binary_and_quotes_exact_entry_without_shell(
    native, monkeypatch
):
    _api, _dll = native

    def directory(buffer, _capacity):
        buffer.value = r"C:\Windows\System32"
        return len(buffer.value)

    system = SimpleNamespace(GetSystemDirectoryW=Mock(side_effect=directory))
    monkeypatch.setattr(ras.ctypes, "WinDLL", lambda *_a, **_kw: system)
    launch = Mock()
    monkeypatch.setattr(ras.os, "startfile", launch, raising=False)
    ras.launch_dialer(_profile(name='Café "VPN" & $x'))
    args = launch.call_args.args
    assert args[0] == r"C:\Windows\System32\rasphone.exe" and args[1] == "open"
    assert args[2] == ras.subprocess.list2cmdline(["-f", BOOK, "-d", 'Café "VPN" & $x'])
    assert "password" not in args[2].lower()


def _controller(profiles=(), sessions=()):
    api = SimpleNamespace(
        profiles=Mock(return_value=list(profiles)),
        sessions=Mock(return_value=list(sessions)),
        disconnect=Mock(),
    )
    idle = []
    dial = Mock()
    value = WindowsVpn(
        api=api, dial=dial, idle_add=lambda callback: idle.append(callback)
    )
    value.changed = Mock()
    return value, api, dial, idle


def test_profile_identity_survives_rename_and_disambiguates_phonebooks():
    first = _profile()
    second = _profile(
        phonebook=r"C:\ProgramData\rasphone.pbk", identifier=GUID2, all_users=True
    )
    value, api, dial, _idle = _controller([first, second], [_session(second)])
    choices = value.connections()
    assert len({c.label for c in choices}) == 2
    assert sum(c.is_active for c in choices) == 1
    renamed = replace(first, name="Renamed VPN")
    api.profiles.return_value = [renamed, second]
    value.toggle(first.key)
    dial.assert_called_once_with(renamed)


def test_deleted_or_replaced_menu_profile_never_launches_wrong_entry():
    profile = _profile()
    value, api, dial, _idle = _controller([profile])
    value.connections()
    api.profiles.return_value = [replace(profile, identifier=GUID2)]
    value.toggle(profile.key)
    dial.assert_not_called()
    api.profiles.side_effect = OSError(5, "denied")
    assert value.connections() == []
    api.profiles.side_effect = None
    value.toggle(profile.key)
    dial.assert_not_called()


def test_disconnect_keeps_gtk_responsive_deduplicates_and_cleans_on_stop():
    profile = _profile()
    value, api, _dial, idle = _controller([profile], [_session(profile)])
    started, release = threading.Event(), threading.Event()

    def disconnect(_sessions):
        started.set()
        assert release.wait(3)

    api.disconnect.side_effect = disconnect
    value.connections()
    value.toggle(profile.key)
    assert started.wait(1)
    assert value.connections()[0].is_pending
    value.toggle(profile.key)
    assert api.disconnect.call_count == 1
    release.set()
    value._workers[profile.key].join(2)
    # A completed worker still waits for GTK publication, preventing a second request.
    value.toggle(profile.key)
    assert api.disconnect.call_count == 1
    changed = value.changed
    value.stop()
    assert not value._workers
    for callback in idle:
        callback()
    changed.assert_not_called()


def test_observed_external_disconnect_changes_checkmark_and_owned_state():
    profile = _profile()
    value, api, _dial, _idle = _controller(
        [profile], [_session(profile, can_disconnect=False)]
    )
    row = value.connections()[0]
    assert row.is_active and not row.can_toggle
    value.toggle(profile.key)
    api.disconnect.assert_not_called()
    api.sessions.return_value = []
    row = value.connections()[0]
    assert not row.is_active and row.can_toggle


def test_shared_vpn_menu_uses_observed_checkmark_and_disables_pending():
    from docking.applets.network.applet import NetworkApplet

    rows = [
        VpnConnection("a", "Connected", True),
        VpnConnection("b", "Disconnecting", True, True),
    ]
    manager = SimpleNamespace(vpn_connections=lambda: rows, toggle_vpn=Mock())
    applet = object.__new__(NetworkApplet)
    item = applet._build_vpn_connections_submenu(manager)
    children = item.get_submenu().get_children()
    assert [c.get_active() for c in children] == [True, True]
    assert [c.get_sensitive() for c in children] == [True, False]


def test_stop_waits_for_owned_ras_teardown_without_publishing_after_shutdown():
    profile = _profile()
    value, api, _dial, idle = _controller([profile], [_session(profile)])
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def disconnect(_sessions):
        started.set()
        assert release.wait(3)
        finished.set()

    api.disconnect.side_effect = disconnect
    value.connections()
    value.toggle(profile.key)
    assert started.wait(1)
    changed = value.changed
    timer = threading.Timer(0.1, release.set)
    timer.start()
    value.stop()
    timer.join()
    assert finished.is_set() and not value._workers
    for callback in idle:
        callback()
    changed.assert_not_called()


def test_native_ras_inventory_can_be_read_concurrently_without_changes():
    import sys

    if sys.platform != "win32":
        pytest.skip("Native Windows RAS API")
    from concurrent.futures import ThreadPoolExecutor

    native = ras.ras_api()
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(
            executor.map(lambda _: (native.profiles(), native.sessions()), range(8))
        )
    assert all(profiles == values[0][0] for profiles, _sessions in values)
