"""Core Audio capture aggregation, thread ownership and failed-change readback."""

import ctypes
from contextlib import contextmanager
from dataclasses import replace

import pytest

from docking.applets.micshield.implementations.windows import audio as native
from docking.applets.micshield.implementations.windows import backend as backend
from docking.applets.micshield.state import MicShieldState, MicStream, build_tooltip
from docking.platform import environment
from docking.platform.composition.windows import WindowsAppletBackendFactories


class CaptureAPI:
    def __init__(self):
        self.endpoints = (
            native.CaptureEndpoint(
                "first", False, (MicStream("session", "Recorder", 42),)
            ),
            native.CaptureEndpoint("non-default", True),
        )
        self.writes = []
        self.accept = True

    def snapshot(self):
        return self.endpoints

    def set_muted(self, muted):
        self.writes.append(muted)
        if self.accept:
            self.endpoints = tuple(replace(e, muted=muted) for e in self.endpoints)
        return self.accept


@pytest.fixture
def capture():
    api = CaptureAPI()
    return backend.WindowsMicShieldBackend(api=api), api


def test_factory_selects_windows_without_importing_pactl(monkeypatch):
    import sys

    monkeypatch.setattr(environment, "is_windows", lambda: True)
    monkeypatch.setitem(
        sys.modules, "docking.applets.micshield.implementations.unix.backend", None
    )
    assert isinstance(
        WindowsAppletBackendFactories().micshield(), backend.WindowsMicShieldBackend
    )


def test_every_active_input_counts_in_aggregate_state(capture):
    client, api = capture
    state = client.get_state()
    assert state.available and state.active and state.muted is False
    assert state.streams[0].pid == 42
    assert client.set_muted(True)
    assert client.get_state().muted
    assert all(e.muted for e in api.endpoints)
    # No default endpoint is selected; a non-default input can disprove mute.
    api.endpoints = (
        replace(api.endpoints[0], muted=True),
        replace(api.endpoints[1], muted=False),
    )
    assert client.get_state().muted is False


def test_toggle_queries_fresh_state_and_unknown_state_only_requests_mute(capture):
    client, api = capture
    api.endpoints = tuple(replace(e, muted=True) for e in api.endpoints)
    assert client.toggle_muted() and api.writes == [False]
    api.endpoints = tuple(replace(e, muted=None) for e in api.endpoints)
    assert client.get_state().muted is None
    assert client.toggle_muted() and api.writes == [False, True]


def test_query_failure_is_unknown_instead_of_idle_or_muted(capture):
    client, api = capture
    api.endpoints = (native.CaptureEndpoint("unknown", None, (), False),)
    state = client.get_state()
    assert state.available and state.muted is None and state.active is None
    tooltip = build_tooltip(state)
    assert "mute state unknown" in tooltip and "activity unknown" in tooltip
    assert "Microphone idle" not in tooltip and "Microphone muted" not in tooltip
    api.endpoints += (native.CaptureEndpoint("known", False, (MicStream("s", "App"),)),)
    assert client.get_state().active and client.get_state().muted is False


def test_partial_or_denied_mute_does_not_become_confirmed_mute(capture):
    client, api = capture
    api.accept = False
    assert not client.set_muted(True) and client.get_state().muted is False
    api.set_muted = lambda _: True  # provider accepts but does not complete
    assert not client.set_muted(True) and client.get_state().muted is False


def test_no_device_service_failure_and_stop_reject_new_writes(capture):
    client, api = capture
    api.endpoints = ()
    assert not client.get_state().available
    assert not client.toggle_muted()

    def fail():
        raise OSError("audio service unavailable")

    api.snapshot = fail
    assert client.get_state() == MicShieldState(False, None, None, error=True)
    assert "state unavailable" in build_tooltip(client.get_state())
    client.stop()
    assert not client.set_muted(True) and api.writes == []
    assert not client.open_settings()


def test_windows_privacy_settings_remain_available_without_a_capture_device(
    capture, monkeypatch
):
    client, api = capture
    api.endpoints = ()
    opened = []
    monkeypatch.setattr(backend.os, "startfile", opened.append, raising=False)
    assert client.settings_available() and client.open_settings()
    assert opened == ["ms-settings:privacy-microphone"]


class AudioRuntime:
    def __init__(self, monkeypatch):
        self.events, self.acquired, self.released, self.allocations = [], [], [], []
        self.strings = []
        self.mutes = {21: False, 31: True}
        self.fail = None
        self.session_state = 1
        self.pid_status = 0
        self.writes = []

        @contextmanager
        def apartment(*, multithreaded=False):
            assert multithreaded
            self.events.append("enter")
            try:
                yield object()
            finally:
                assert sorted(self.acquired) == sorted(self.released)
                assert not self.allocations
                self.events.append("exit")

        monkeypatch.setattr(native, "apartment", apartment)
        monkeypatch.setattr(native, "method", self.method)
        monkeypatch.setattr(native, "bind", self.bind)
        monkeypatch.setattr(native, "release", lambda p: self.released.append(p.value))
        monkeypatch.setattr(native, "_process_name", lambda pid: "recorder")

    def write(self, output, kind, value):
        ctypes.cast(output, ctypes.POINTER(kind))[0] = value

    def object(self, output, value):
        self.acquired.append(value)
        self.write(output, ctypes.c_void_p, value)

    def text(self, output, text):
        value = ctypes.create_unicode_buffer(text)
        self.strings.append(value)
        pointer = ctypes.addressof(value)
        self.allocations.append(pointer)
        self.write(output, ctypes.c_void_p, pointer)

    def bind(self, library, name, result, *arguments):
        if name == "CoCreateInstance":

            def create(*args):
                self.object(args[-1], 10)
                return -1 if self.fail == "create" else 0

            return create
        assert name == "CoTaskMemFree"
        return lambda pointer: self.allocations.remove(pointer.value)

    def method(self, pointer, index, result, *kinds):
        identity = pointer.value

        def invoke(this, *args):
            assert self.events[-1] == "enter"
            key = (identity, index)
            if key == (10, 3):
                assert args[:2] == (1, 1)  # capture, all active devices
                self.object(args[-1], 11)
            elif key == (11, 3):
                self.write(args[0], ctypes.c_uint32, 2)
            elif key == (11, 4):
                self.object(args[-1], 20 + 10 * args[0])
            elif identity in (20, 30) and index == 5:
                self.text(args[0], f"input-{identity}")
            elif identity in (20, 30) and index == 3:
                iid = bytes(ctypes.cast(args[0], ctypes.POINTER(native.GUID)).contents)
                assert iid in (bytes(native._VOLUME), bytes(native._SESSION_MANAGER))
                self.object(
                    args[-1], identity + (1 if iid == bytes(native._VOLUME) else 2)
                )
            elif identity in (21, 31) and index == 15:
                self.write(args[0], ctypes.c_int32, self.mutes[identity])
            elif identity in (21, 31) and index == 14:
                self.writes.append((identity, bool(args[0])))
                if key != self.fail:
                    self.mutes[identity] = bool(args[0])
            elif identity in (22, 32) and index == 5:
                self.object(args[0], identity + 1)
            elif identity in (23, 33) and index == 3:
                self.write(args[0], ctypes.c_int32, 2 if identity == 23 else 0)
            elif key == (23, 4):
                self.object(args[-1], 24 + args[0])
            elif identity in (24, 25) and index == 3:
                self.write(
                    args[0], ctypes.c_int32, self.session_state if identity == 24 else 0
                )
            elif key == (24, 4):
                self.text(args[0], "Recorder")
            elif key == (24, 0):
                self.object(args[-1], 26)
            elif key == (26, 13):
                self.text(args[0], "session-instance")
            elif key == (26, 14):
                self.write(args[0], ctypes.c_uint32, 42)
                return -1 if key == self.fail else self.pid_status
            else:
                raise AssertionError(key)
            return -1 if key == self.fail else 0

        return invoke


@pytest.fixture
def runtime(monkeypatch):
    return AudioRuntime(monkeypatch)


def test_capture_interfaces_and_strings_are_released_inside_their_mta(runtime):
    endpoints = native.WindowsCaptureAPI().snapshot()
    assert len(endpoints) == 2 and endpoints[1].muted
    assert endpoints[0].streams == (
        MicStream("session-instance", "Recorder", 42, "Recorder"),
    )
    assert runtime.events == ["enter", "exit"]


@pytest.mark.parametrize("failure", ["create", (10, 3), (11, 4), (20, 5)])
def test_partial_acquisition_failure_releases_interfaces_and_strings(runtime, failure):
    runtime.fail = failure
    with pytest.raises(OSError):
        native.WindowsCaptureAPI().snapshot()
    assert runtime.events[-1] == "exit"


@pytest.mark.parametrize("failure", [(20, 3), (21, 15)])
def test_unreadable_endpoint_mute_stays_unknown_and_other_inputs_are_read(
    runtime, failure
):
    runtime.fail = failure
    endpoints = native.WindowsCaptureAPI().snapshot()
    assert endpoints[0].muted is None and endpoints[1].muted is True


def test_unknown_session_state_is_not_reported_as_idle(runtime):
    runtime.session_state = 9
    endpoint = native.WindowsCaptureAPI().snapshot()[0]
    assert not endpoint.activity_known and endpoint.streams == ()


@pytest.mark.parametrize("failure", [(24, 4), (24, 0), (26, 13), (26, 14)])
def test_metadata_failure_never_hides_known_active_capture(runtime, failure):
    runtime.fail = failure
    endpoint = native.WindowsCaptureAPI().snapshot()[0]
    assert endpoint.activity_known and len(endpoint.streams) == 1


def test_multi_process_session_does_not_claim_one_pid_as_the_owner(runtime):
    runtime.pid_status = 0x0889000D
    assert native.WindowsCaptureAPI().snapshot()[0].streams[0].pid is None


def test_mute_continues_other_inputs_after_one_endpoint_fails(runtime):
    runtime.fail = (21, 14)
    assert not native.WindowsCaptureAPI().set_muted(True)
    assert runtime.writes == [(21, True), (31, True)]
    assert not runtime.mutes[21] and runtime.mutes[31]
