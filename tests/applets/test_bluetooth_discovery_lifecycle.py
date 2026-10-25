"""Discovery sessions must be released when their UI owner no longer wants them."""

from queue import Empty, Queue
from threading import Thread

import pytest
from gi.repository import Gtk

import docking.applets.bluetooth.applet as bluetooth
from docking.applets.bluetooth.backend import BluetoothCapabilities
from docking.applets.bluetooth.state import BluetoothAdapterState, BluetoothState
from docking.applets.worker import BackgroundWorker
from docking.core.config import Config

FIRST = "/org/bluez/hci0"
SECOND = "/org/bluez/hci1"


class _DiscoveryBackend:
    capabilities = BluetoothCapabilities()

    def close(self):
        pass

    def __init__(self):
        self.sessions = set()
        self.powered = {FIRST, SECOND}
        self.start_calls = []
        self.stop_calls = []

    def get_state(self, active_adapter_path=""):
        return BluetoothState(
            available=True,
            adapters=tuple(
                BluetoothAdapterState(
                    path=path,
                    name=path,
                    alias=path,
                    powered=path in self.powered,
                    discovering=path in self.sessions,
                    address="AA:BB:CC:DD:EE:FF",
                )
                for path in (FIRST, SECOND)
            ),
            devices=(),
            active_adapter_path=active_adapter_path or FIRST,
        )

    def start_discovery(self, adapter_path):
        self.start_calls.append(adapter_path)
        # BlueZ rejects a second session for the same client and adapter.
        if adapter_path in self.sessions:
            return False
        self.sessions.add(adapter_path)
        return True

    def stop_discovery(self, adapter_path, quiet=False):
        self.stop_calls.append(adapter_path)
        self.sessions.discard(adapter_path)
        return True

    def set_adapter_power(self, adapter_path, powered):
        if powered:
            self.powered.add(adapter_path)
        else:
            self.powered.discard(adapter_path)
            self.sessions.discard(adapter_path)
        return True


class _Replies:
    def __init__(self):
        self.queue = Queue()
        self.threads = []
        self.worker = BackgroundWorker(
            idle_add=self._enqueue, thread_factory=self._thread
        )

    def _enqueue(self, callback, *args):
        self.queue.put((callback, args))

    def _thread(self, **kwargs):
        thread = Thread(**kwargs)
        self.threads.append(thread)
        return thread

    def next(self):
        callback, args = self.queue.get(timeout=3)
        return lambda: callback(*args)

    def drain(self):
        for _ in range(30):
            for thread in self.threads:
                thread.join(timeout=3)
                assert not thread.is_alive()
            try:
                callback, args = self.queue.get_nowait()
            except Empty:
                return
            callback(*args)
        raise AssertionError("Discovery requests did not settle")


def _applet(monkeypatch):
    backend = _DiscoveryBackend()
    monkeypatch.setattr(bluetooth, "create_backend", lambda: backend)
    applet = bluetooth.BluetoothApplet(
        48, Config(applet_prefs={"bluetooth": {"continuous_discovery": False}})
    )
    applet._on_poll_result(backend.get_state())
    applet._continuous_discovery = True
    replies = _Replies()
    applet._worker = replies.worker
    return applet, backend, replies


def test_poll_reply_cannot_start_discovery_after_stop(monkeypatch):
    applet, backend, replies = _applet(monkeypatch)
    applet._tick()
    old_reply = replies.next()
    applet.stop()

    old_reply()
    replies.drain()

    assert backend.start_calls == []
    assert backend.sessions == set()


def test_pending_discovery_is_released_after_stop(monkeypatch):
    applet, backend, replies = _applet(monkeypatch)
    applet._ensure_discovery()
    late_reply = replies.next()
    assert backend.sessions == {FIRST}
    applet.stop()

    late_reply()
    replies.drain()

    assert backend.sessions == set()
    assert backend.stop_calls == [FIRST]


def test_pending_discovery_is_released_when_disabled(monkeypatch):
    applet, backend, replies = _applet(monkeypatch)
    applet._ensure_discovery()
    late_reply = replies.next()
    toggle = Gtk.CheckMenuItem()
    toggle.set_active(False)
    applet._on_continuous_discovery_toggled(toggle)
    replies.drain()

    late_reply()
    replies.drain()

    assert backend.sessions == set()
    assert backend.stop_calls == [FIRST]


def test_stale_idle_poll_cannot_lose_a_started_session(monkeypatch):
    applet, backend, replies = _applet(monkeypatch)
    applet._tick()
    old_reply = replies.next()
    applet._ensure_discovery()
    replies.drain()
    assert backend.sessions == {FIRST}

    old_reply()
    replies.drain()
    applet.stop()
    replies.drain()

    assert backend.sessions == set()
    assert backend.stop_calls == [FIRST]


@pytest.mark.parametrize("pending", [False, True])
def test_switching_adapter_releases_previous_discovery(monkeypatch, pending):
    applet, backend, replies = _applet(monkeypatch)
    applet._ensure_discovery()
    if pending:
        late_reply = replies.next()
    else:
        replies.drain()
    assert backend.sessions == {FIRST}
    radio = Gtk.RadioMenuItem()
    radio.set_active(True)

    applet._on_select_adapter(radio, SECOND)
    replies.drain()
    if pending:
        late_reply()
        replies.drain()

    assert backend.sessions == {SECOND}
    assert backend.stop_calls == [FIRST]
    applet.stop()
    replies.drain()
    assert backend.sessions == set()


def test_late_power_reply_preserves_new_adapter_discovery(monkeypatch):
    applet, backend, replies = _applet(monkeypatch)
    applet._ensure_discovery()
    replies.drain()
    applet._set_power_async(target=False)
    power_reply = replies.next()
    assert FIRST not in backend.powered

    # The old power result is delayed past the discovery suppression window.
    applet._suppress_discovery_until = 0
    radio = Gtk.RadioMenuItem()
    radio.set_active(True)
    applet._on_select_adapter(radio, SECOND)
    replies.drain()
    assert backend.sessions == {SECOND}

    power_reply()
    applet.stop()
    replies.drain()

    assert backend.sessions == set()
    assert backend.stop_calls == [FIRST, SECOND]


def test_queued_discovery_is_rejected_after_stop_and_restart(monkeypatch):
    applet, old_backend, _ = _applet(monkeypatch)
    tasks = []

    class PendingWorker:
        def run_guarded(self, **task):
            tasks.append(task)
            return True

    applet._worker = PendingWorker()
    applet._ensure_discovery()
    old_task = tasks.pop()
    applet.stop()
    assert not old_task["fn"]()
    assert old_backend.start_calls == []

    replacement = _DiscoveryBackend()
    monkeypatch.setattr(bluetooth, "create_backend", lambda: replacement)
    monkeypatch.setattr(bluetooth.GLib, "timeout_add_seconds", lambda *args: 7)
    monkeypatch.setattr(bluetooth.GLib, "source_remove", lambda *args: True)
    applet.start(lambda: None)
    assert applet._backend is replacement
    assert not old_task["fn"]()
    tasks.clear()  # Ignore the queued state refresh.
    applet._ensure_discovery()
    current = tasks.pop()
    assert current["fn"]()
    current["on_result"](True)
    applet.stop()
    assert not replacement.sessions


@pytest.mark.parametrize("action", ["power", "device", "discovery"])
def test_old_backend_reply_cannot_change_restarted_applet(monkeypatch, action):
    applet, old_backend, replies = _applet(monkeypatch)
    if action == "power":
        applet._set_power_async(target=False)
    elif action == "device":
        applet._run_async(lambda: True)
    else:
        applet._ensure_discovery()
    late_reply = replies.next()
    applet.stop()
    replacement = _DiscoveryBackend()
    monkeypatch.setattr(bluetooth, "create_backend", lambda: replacement)
    monkeypatch.setattr(bluetooth.GLib, "timeout_add_seconds", lambda *args: 7)
    monkeypatch.setattr(bluetooth.GLib, "source_remove", lambda *args: True)
    try:
        applet.start(lambda: None)
        replies.drain()
        state = applet._state
        generation = applet._poll_generation
        sessions = applet._local_discovery_paths.copy()
        applet._power_transition_in_progress = True

        late_reply()
        replies.drain()

        assert applet._state is state
        assert applet._poll_generation == generation
        assert applet._power_transition_in_progress
        assert applet._local_discovery_paths == sessions
        assert not old_backend.sessions
        assert not replacement.stop_calls
    finally:
        applet.stop()
        replies.drain()


@pytest.mark.parametrize("action", ["power", "device"])
def test_queued_action_cannot_operate_on_replacement_backend(monkeypatch, action):
    applet, old_backend, _ = _applet(monkeypatch)
    tasks = []
    calls = []

    class PendingWorker:
        def run(self, **task):
            tasks.append(task)

        def run_guarded(self, **task):
            tasks.append(task)
            return True

    applet._worker = PendingWorker()
    if action == "power":
        applet._set_power_async(target=False)
    else:
        applet._run_async(lambda: calls.append(True) or True)
    queued = tasks.pop()
    applet.stop()
    replacement = _DiscoveryBackend()
    monkeypatch.setattr(bluetooth, "create_backend", lambda: replacement)
    monkeypatch.setattr(bluetooth.GLib, "timeout_add_seconds", lambda *args: 7)
    monkeypatch.setattr(bluetooth.GLib, "source_remove", lambda *args: True)
    try:
        applet.start(lambda: None)
        queued["on_result"](queued["fn"]())
        assert not calls
        assert old_backend.powered == replacement.powered == {FIRST, SECOND}
        assert not applet._power_transition_in_progress
    finally:
        applet.stop()


def test_discovery_waits_for_pending_power_authorization(monkeypatch):
    applet, backend, replies = _applet(monkeypatch)
    applet._power_transition_in_progress = True
    applet._power_transition_adapter_path = FIRST
    applet._suppress_discovery_until = 0
    applet._ensure_discovery()
    replies.drain()
    assert not backend.start_calls
    applet.stop()
