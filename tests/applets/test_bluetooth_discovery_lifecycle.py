"""Discovery sessions must be released when their UI owner no longer wants them."""

from queue import Empty, Queue
from threading import Thread

import pytest
from gi.repository import Gtk

import docking.applets.bluetooth.applet as bluetooth
from docking.applets.bluetooth.state import BluetoothAdapterState, BluetoothState
from docking.applets.worker import BackgroundWorker
from docking.core.config import Config

FIRST = "/org/bluez/hci0"
SECOND = "/org/bluez/hci1"


class _DiscoveryBackend:
    def __init__(self):
        self.sessions = set()
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
                    powered=True,
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
    monkeypatch.setattr(bluetooth, "BluezBackend", lambda: backend)
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
