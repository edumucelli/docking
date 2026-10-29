"""Windows shortcut parsing, thread ownership and main-loop lifecycle."""

from __future__ import annotations

import threading
import time
from queue import Empty, Queue

import pytest

from docking.platform.backends.windows.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
)
from docking.search.services.global_shortcuts import GlobalShortcutsState
from docking.search.services.windows_shortcuts import (
    WindowsGlobalShortcutsService,
    parse_windows_shortcut,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("CTRL+ALT+space", (MOD_CONTROL | MOD_ALT, 0x20)),
        ("ctrl + shift + F5", (MOD_CONTROL | MOD_SHIFT, 0x74)),
        ("LOGO+Return", (MOD_WIN, 0x0D)),
        ("ALT+Page_Down", (MOD_ALT, 0x22)),
        ("F24", (0, 0x87)),
        ("CTRL+KP_3", (MOD_CONTROL, 0x63)),
        ("CTRL+A", (MOD_CONTROL, "a")),
        ("CTRL+semicolon", (MOD_CONTROL, ";")),
        ("CTRL+eacute", (MOD_CONTROL, "é")),
    ],
)
def test_supported_shortcuts_keep_modifiers_and_physical_key_information(
    text, expected
):
    assert parse_windows_shortcut(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "+",
        "CTRL++space",
        "CTRL",
        "NUM+space",
        "HYPER+space",
        "CTRL+F12",
        "a",
        "SHIFT+space",
        "CTRL+not_a_real_key",
    ],
)
def test_unusable_shortcuts_are_reported_before_native_registration(text):
    with pytest.raises(ValueError):
        parse_windows_shortcut(text)


class _Idle:
    def __init__(self):
        self.callbacks = {}
        self.serial = 0
        self.lock = threading.RLock()

    def add(self, callback):
        with self.lock:
            self.serial += 1
            self.callbacks[self.serial] = callback
            return self.serial

    def remove(self, source):
        with self.lock:
            return self.callbacks.pop(source, None) is not None

    def flush(self):
        with self.lock:
            callbacks = list(self.callbacks.values())
            self.callbacks.clear()
        for callback in callbacks:
            assert callback() is False


class _Native:
    def __init__(self, *, error=None, gate=None):
        self.registered = threading.Event()
        self.released = threading.Event()
        self.events = Queue()
        self.error = error
        self.gate = gate
        self.owner = None
        self.release_owner = None
        self.registration = None
        self.read_count = 0

    def register(self, modifiers, key):
        self.owner = threading.get_ident()
        self.registration = modifiers, key
        self.registered.set()
        if self.gate:
            self.gate.wait(2)
        if self.error:
            raise self.error

    def activation(self):
        self.read_count += 1
        try:
            event = self.events.get_nowait()
            if isinstance(event, Exception):
                raise event
            return event
        except Empty:
            return None

    def unregister(self):
        self.release_owner = threading.get_ident()
        self.released.set()


def _wait(predicate):
    deadline = time.monotonic() + 2
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Worker did not reach the expected state")
        time.sleep(0.005)


def _service(native_factory, idle, activated, statuses):
    return WindowsGlobalShortcutsService(
        preferred_trigger="CTRL+ALT+space",
        trigger_description="Ctrl+Alt+Space",
        on_activated=lambda event: activated.append((threading.get_ident(), event)),
        on_status_changed=lambda status: statuses.append(
            (threading.get_ident(), status)
        ),
        schedule_idle=idle.add,
        remove_source=idle.remove,
        native_factory=native_factory,
    )


def _flush_until(idle, predicate):
    def flush():
        idle.flush()
        return predicate()

    _wait(flush)


def test_native_registration_and_release_share_one_worker_and_callbacks_use_main_loop():
    native, idle = _Native(), _Idle()
    activated, statuses = [], []
    main = threading.get_ident()
    service = _service(lambda: native, idle, activated, statuses)
    try:
        service.start()
        service.start()
        assert native.registered.wait(1)
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.BOUND)
        assert native.registration == (MOD_CONTROL | MOD_ALT, 0x20)
        assert service.status.binding.trigger_description == "Ctrl+Alt+Space"
        native.events.put(1234)
        _wait(lambda: native.events.empty())
        assert activated == []
        _flush_until(idle, lambda: bool(activated))
        assert activated[0][0] == main
        assert activated[0][1].timestamp == 1234
        assert all(owner == main for owner, _status in statuses)
        assert native.owner != main
    finally:
        service.stop()
    assert native.released.is_set()
    assert native.release_owner == native.owner
    assert service.status.state is GlobalShortcutsState.STOPPED
    assert service.status.binding is None
    assert idle.callbacks == {}
    assert not service._thread.is_alive()


def test_pending_activations_are_bounded_and_old_callbacks_cannot_survive_restart():
    natives = [_Native(), _Native()]
    created = iter(natives)
    idle, activated, statuses = _Idle(), [], []
    service = _service(lambda: next(created), idle, activated, statuses)
    try:
        service.start()
        assert natives[0].registered.wait(1)
        for timestamp in range(30):
            natives[0].events.put(timestamp)
        _wait(lambda: natives[0].events.empty())
        assert len(idle.callbacks) <= 2  # one ready notification and one activation
        old_callbacks = list(idle.callbacks.values())
        service.stop()
        assert idle.callbacks == {}
        service.start()
        assert natives[1].registered.wait(1)
        for callback in old_callbacks:
            callback()
        assert activated == []
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.BOUND)
        natives[1].events.put(4321)
        _flush_until(idle, lambda: bool(activated))
        assert [event.timestamp for _owner, event in activated] == [4321]
    finally:
        service.stop()
    assert all(native.released.is_set() for native in natives)


def test_conflict_has_no_binding_and_can_be_retried_after_stop():
    error = OSError("Already registered")
    error.winerror = 1409
    natives = [_Native(error=error), _Native()]
    created = iter(natives)
    idle, activated, statuses = _Idle(), [], []
    service = _service(lambda: next(created), idle, activated, statuses)
    try:
        service.start()
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.ERROR)
        assert service.status.message == "shortcut is already in use"
        assert service.status.binding is None
        assert not natives[0].released.is_set()
        service.stop()
        service.start()
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.BOUND)
    finally:
        service.stop()
    assert natives[1].released.is_set()


def test_initial_conflict_recovers_without_restarting_or_changing_preferences():
    error = OSError("Already registered")
    error.winerror = 1409
    native, idle = _Native(error=error), _Idle()
    activated, statuses = [], []
    service = _service(lambda: native, idle, activated, statuses)
    try:
        service.start()
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.ERROR)
        owner = native.owner
        native.error = None
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.BOUND)
        assert native.owner == owner
        native.events.put(321)
        _flush_until(idle, lambda: bool(activated))
        assert activated[0][1].timestamp == 321
    finally:
        service.stop()
    assert native.released.is_set()


@pytest.mark.parametrize(
    "error", [ValueError("unavailable layout"), OSError("conflict")]
)
def test_layout_rebind_errors_are_reported_and_recover_on_the_same_worker(error):
    native, idle = _Native(), _Idle()
    activated, statuses = [], []
    service = _service(lambda: native, idle, activated, statuses)
    try:
        service.start()
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.BOUND)
        native.events.put(error)
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.ERROR)
        assert service.status.binding is None
        _flush_until(idle, lambda: service.status.state is GlobalShortcutsState.BOUND)
        assert not native.released.is_set()
        native.events.put(456)
        _flush_until(idle, lambda: bool(activated))
        assert activated[0][1].timestamp == 456
    finally:
        service.stop()
    assert native.release_owner == native.owner


def test_stop_during_registration_releases_late_grab_without_publishing_bound():
    gate = threading.Event()
    native = _Native(gate=gate)
    idle, activated, statuses = _Idle(), [], []
    service = _service(lambda: native, idle, activated, statuses)
    service.start()
    assert native.registered.wait(1)
    service.stop()
    gate.set()
    assert native.released.wait(1)
    idle.flush()
    assert service.status.state is GlobalShortcutsState.STOPPED
    assert all(
        status.state is not GlobalShortcutsState.BOUND for _owner, status in statuses
    )
    assert activated == []
