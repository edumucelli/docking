"""WinRT network metadata ABI and ownership across optional/failing queries."""

import ctypes
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.network import windows_profiles as profiles
from docking.platform.backends.windows.com import GUID

ID = "{9cd793bb-5594-4e16-b57a-e8a43f0a4b5f}"


def _write(pointer, kind, value):
    ctypes.cast(pointer, ctypes.POINTER(kind))[0] = value


@pytest.fixture
def runtime(monkeypatch):
    class Runtime:
        def __init__(self):
            self.strings = {}
            self.deleted = []
            self.released = []
            self.events = []
            self.ssid = " Café 🛜 "
            self.bars = 4
            self.connected = True
            self.is_wifi = True
            self.failure = None
            self.null_bars = False
            self.initialize_result = 0
            self.memory = []
            self.dll = SimpleNamespace(
                RoInitialize=Mock(side_effect=lambda _mode: self.initialize_result),
                RoUninitialize=Mock(),
                WindowsCreateString=Mock(side_effect=self.create_string),
                WindowsDeleteString=Mock(side_effect=self.delete_string),
                WindowsGetStringRawBuffer=Mock(side_effect=self.raw_string),
                RoGetActivationFactory=Mock(side_effect=self.factory),
            )

        def create_string(self, source, length, output):
            text = ctypes.string_at(source, length * 2).decode("utf-16-le")
            assert text == "Windows.Networking.Connectivity.NetworkInformation"
            self.strings[100] = text
            _write(output, ctypes.c_void_p, 100)
            return 0

        def delete_string(self, pointer):
            self.deleted.append(pointer.value)
            return 0

        def raw_string(self, pointer, length):
            text = self.strings[pointer.value].encode(
                "utf-16-le", errors="surrogatepass"
            )
            _write(length, ctypes.c_uint32, len(text) // 2)
            value = ctypes.create_string_buffer(text)
            self.memory.append(value)
            return ctypes.addressof(value)

        def factory(self, name, iid, output):
            assert self.strings[name.value].endswith(".NetworkInformation")
            assert bytes(ctypes.cast(iid, ctypes.POINTER(GUID)).contents) == bytes(
                profiles._STATICS
            )
            _write(output, ctypes.c_void_p, 1)
            return -1 if self.failure == "factory" else 0

        def method(self, pointer, slot, _result, *_arguments):
            def invoke(_this, *args):
                identity = pointer.value
                self.events.append((identity, slot))
                out = args[-1]
                if (identity, slot) == (
                    1,
                    6,
                ):  # NetworkInformation.GetConnectionProfiles
                    _write(out, ctypes.c_void_p, 2)
                elif (identity, slot) == (2, 7):  # IVectorView.Size
                    _write(out, ctypes.c_uint32, 1)
                elif (identity, slot) == (2, 6):  # IVectorView.GetAt
                    assert args[0] == 0
                    _write(out, ctypes.c_void_p, 3)
                elif (identity, slot) == (
                    3,
                    7,
                ):  # IConnectionProfile.GetConnectivityLevel
                    _write(out, ctypes.c_int32, int(self.connected))
                elif (identity, slot) == (3, 11):
                    _write(out, ctypes.c_void_p, 4)
                elif (identity, slot) == (4, 10):
                    _write(out, GUID, GUID.parse(ID))
                elif (identity, slot) == (3, 0):  # QueryInterface(IConnectionProfile2)
                    assert bytes(
                        ctypes.cast(args[0], ctypes.POINTER(GUID)).contents
                    ) == bytes(profiles._PROFILE2)
                    _write(out, ctypes.c_void_p, 5)
                elif (identity, slot) == (5, 7):
                    _write(out, ctypes.c_ubyte, self.is_wifi)
                elif (identity, slot) == (5, 9):
                    _write(out, ctypes.c_void_p, 6)
                elif (identity, slot) == (6, 6):
                    self.strings[101] = self.ssid
                    _write(out, ctypes.c_void_p, 101)
                elif (identity, slot) == (5, 11):
                    _write(out, ctypes.c_void_p, None if self.null_bars else 7)
                elif (identity, slot) == (7, 6):
                    _write(out, ctypes.c_ubyte, self.bars)
                else:
                    raise AssertionError((identity, slot, args))
                return -1 if self.failure == (identity, slot) else 0

            return invoke

    value = Runtime()
    monkeypatch.setattr(
        profiles.ctypes, "WinDLL", lambda *_a, **_kw: value.dll, raising=False
    )
    monkeypatch.setattr(profiles, "method", value.method)
    monkeypatch.setattr(
        profiles, "release", lambda pointer: value.released.append(pointer.value)
    )
    return value, profiles._ProfileAPI()


def test_profile_keeps_exact_ssid_stable_adapter_identity_and_signal_bars(runtime):
    value, api = runtime
    assert api.profiles() == [profiles.ConnectionProfile(ID, True, " Café 🛜 ", 80)]
    assert sorted(value.released) == list(range(1, 8))
    assert value.deleted == [101, 100]
    value.dll.RoUninitialize.assert_called_once()


def test_wired_profile_never_queries_wifi_details_or_signal(runtime):
    value, api = runtime
    value.is_wifi = False
    assert api.profiles() == [profiles.ConnectionProfile(ID, False)]
    assert (5, 9) not in value.events and (5, 11) not in value.events
    assert sorted(value.released) == [1, 2, 3, 4, 5]


def test_disconnected_saved_profile_is_never_reported_as_current(runtime):
    value, api = runtime
    value.connected = False
    assert api.profiles() == []
    assert sorted(value.released) == [1, 2, 3]


@pytest.mark.parametrize("fault", ["null", "unknown", "failed_query", "failed_value"])
def test_unknown_signal_retains_ssid_and_never_fabricates_zero(runtime, fault):
    value, api = runtime
    if fault == "null":
        value.null_bars = True
    elif fault == "unknown":
        value.bars = 255
    elif fault == "failed_query":
        value.failure = (5, 11)
    else:
        value.failure = (7, 6)
    assert api.profiles() == [profiles.ConnectionProfile(ID, True, value.ssid, None)]
    assert value.deleted == [101, 100]
    assert len(value.released) == len(set(value.released))


@pytest.mark.parametrize("failure", [(2, 6), (3, 11), (3, 0), (5, 9), (6, 6)])
def test_disappearing_or_denied_profile_releases_partial_results(runtime, failure):
    value, api = runtime
    value.failure = failure
    assert api.profiles() == []
    assert value.released and value.deleted[-1] == 100
    assert len(value.released) == len(set(value.released))
    value.dll.RoUninitialize.assert_called_once()


def test_failed_factory_releases_returned_pointer_and_runtime_string(runtime):
    value, api = runtime
    value.failure = "factory"
    with pytest.raises(OSError):
        api.profiles()
    assert value.released == [1] and value.deleted == [100]
    value.dll.RoUninitialize.assert_called_once()


@pytest.mark.parametrize(
    "status,uninitialize", [(0, True), (1, True), (-2147417850, False)]
)
def test_apartment_lifetime_respects_existing_gtk_apartment(
    runtime, status, uninitialize
):
    value, api = runtime
    value.initialize_result = status
    assert api.profiles()
    assert value.dll.RoUninitialize.called is uninitialize


def test_invalid_utf16_from_system_is_displayed_safely_and_released(runtime):
    value, api = runtime
    value.ssid = "Cafe\ud800"
    assert api.profiles()[0].ssid == "Cafe\ufffd"
    assert value.deleted == [101, 100]
