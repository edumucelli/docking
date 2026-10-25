"""Core Audio ownership, default-device recovery and shared volume controls."""

import ctypes
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from docking.applets.volume import windows
from docking.applets.volume.backend import create_backend
from docking.applets.volume.state import VolumeState
from docking.platform import environment


class AudioRuntime:
    """A failing or changing endpoint, with observable apartment lifetimes."""

    def __init__(self, monkeypatch):
        self.events = []
        self.level = 0.47
        self.muted = False
        self.failure = None
        self.default_id = 20

        @contextmanager
        def apartment():
            self.events.append("enter")
            try:
                yield object()
            finally:
                self.events.append("exit")

        monkeypatch.setattr(windows, "apartment", apartment)
        monkeypatch.setattr(windows, "_bind", lambda *args: self.create)
        monkeypatch.setattr(windows, "method", self.method)
        monkeypatch.setattr(
            windows, "release", lambda p: self.events.append(("release", p.value))
        )

    @staticmethod
    def write(pointer, kind, value):
        ctypes.cast(pointer, ctypes.POINTER(kind))[0] = value

    def create(self, clsid, outer, context, iid, pointer):
        self.write(pointer, ctypes.c_void_p, 10)
        return 0

    def method(self, pointer, index, result, *arguments):
        identity = pointer.value

        def invoke(this, *args):
            assert this.value == identity
            assert self.events[0] == "enter" and self.events[-1] != "exit"
            self.events.append((identity, index))
            if index == self.failure:
                return -2147023728  # HRESULT_FROM_WIN32(ERROR_NOT_FOUND)
            if identity == 10 and index == 4:
                assert args[:2] == (0, 1)  # output, multimedia role
                self.write(args[2], ctypes.c_void_p, self.default_id)
            elif identity == self.default_id and index == 3:
                self.write(args[3], ctypes.c_void_p, identity + 1)
            elif index == 9:
                self.write(args[0], ctypes.c_float, self.level)
            elif index == 15:
                self.write(args[0], ctypes.c_int32, self.muted)
            elif index == 7:
                self.level = args[0]
                assert args[1] is None
            elif index == 14:
                self.muted = bool(args[0])
                assert args[1] is None
            else:
                raise AssertionError((identity, index))
            return 0

        return invoke


@pytest.fixture
def runtime(monkeypatch):
    return AudioRuntime(monkeypatch)


def test_reads_state_and_releases_every_interface_inside_apartment(runtime):
    assert windows.get_state() == VolumeState(47, False)
    assert runtime.events[-4:] == [
        ("release", 21),
        ("release", 20),
        ("release", 10),
        "exit",
    ]


@pytest.mark.parametrize("failure", [4, 3, 9, 15])
def test_failure_releases_acquired_interfaces_and_next_poll_recovers(runtime, failure):
    runtime.failure = failure
    assert windows.get_state() is None
    acquired = {4: [10], 3: [20, 10], 9: [21, 20, 10], 15: [21, 20, 10]}[failure]
    assert [
        e[1]
        for e in runtime.events
        if isinstance(e, tuple) and e[0] == "release" and e[1]
    ] == acquired
    assert runtime.events[-1] == "exit"
    runtime.failure = None
    runtime.events.clear()
    runtime.default_id = 30
    runtime.level = 0.81
    assert windows.get_state() == VolumeState(81, False)
    assert ("release", 31) in runtime.events


@pytest.mark.parametrize("requested,expected", [(-20, 0), (55, 0.55), (120, 1)])
def test_volume_write_clamps_to_native_scalar_range(runtime, requested, expected):
    windows.set_volume(requested)
    assert runtime.level == expected
    assert runtime.events[-1] == "exit"


def test_mute_toggle_reads_current_state_and_keeps_volume(runtime):
    for expected in (True, False):
        runtime.events.clear()
        windows.toggle_mute()
        assert runtime.muted is expected
        assert runtime.level == 0.47
        assert runtime.events.count("enter") == 1
        assert (21, 15) in runtime.events and (21, 14) in runtime.events
        assert runtime.events[-1] == "exit"


@pytest.mark.parametrize(
    "operation,failure", [(windows.set_volume, 7), (windows.toggle_mute, 14)]
)
def test_write_failure_does_not_escape_ui_handler(runtime, operation, failure):
    runtime.failure = failure
    if operation is windows.set_volume:
        operation(60)
    else:
        operation()
    assert runtime.events[-1] == "exit"


def test_windows_backend_is_available_before_an_output_is_connected(monkeypatch):
    monkeypatch.setattr(environment, "is_windows", lambda: True)
    monkeypatch.setattr(windows, "get_state", Mock(return_value=None))
    backend = create_backend()
    assert backend.controls is not None and backend.controls.command == "Core Audio"
    windows.get_state.assert_not_called()
    assert backend.controls.get_state() is None


def test_windows_settings_uses_native_uri_association(monkeypatch):
    from docking.platform.backends.windows import shell

    monkeypatch.setattr(environment, "is_windows", lambda: True)
    launch = Mock(return_value=True)
    monkeypatch.setattr(shell, "open_default_target", launch)
    value = create_backend()
    assert value.settings.available()
    assert value.settings.open()
    launch.assert_called_once_with("ms-settings:sound")


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows Core Audio")
def test_native_reads_from_multiple_apartments_without_changing_audio():
    with ThreadPoolExecutor(max_workers=4) as executor:
        states = list(executor.map(lambda _: windows.get_state(), range(16)))
    for current in states:
        # A desktop with no audio output is valid and can acquire one later.
        assert current is None or (
            0 <= current.volume <= 100 and isinstance(current.muted, bool)
        )
