"""Older background reads must not undo completed user actions."""

from dataclasses import replace
from queue import Queue
from unittest.mock import MagicMock

import pytest

import docking.applets.bluetooth.applet as bluetooth
import docking.applets.micshield.applet as micshield
import docking.applets.micshield.implementations.unix.backend as native_micshield
import docking.applets.music.applet as music
import docking.applets.music.implementations.unix.backend as native_music
import tests.platform_factories as _native_tests_platform_factories
from docking.applets.bluetooth.state import BluetoothAdapterState, BluetoothState
from docking.applets.micshield.state import MicShieldState
from docking.applets.music.state import VOLUME_STEP, MusicState
from docking.applets.worker import BackgroundWorker
from docking.core.config import Config
from tests.platform_factories import make_applet_platform


class _UIReplies:
    """Keep real worker threads, and control when their GTK replies are delivered."""

    def __init__(self):
        self.queue = Queue()
        self.worker = BackgroundWorker(idle_add=self._enqueue)

    def _enqueue(self, callback, *args):
        self.queue.put((callback, args))

    def next(self):
        callback, args = self.queue.get(timeout=3)
        return lambda: callback(*args)


@pytest.mark.parametrize("action", ["next", "volume"])
def test_music_old_poll_does_not_undo_action(monkeypatch, action):
    initial = MusicState(
        available=True,
        title="First song",
        volume_percent=50,
        can_go_next=True,
    )
    backend = MagicMock()
    backend.poll.return_value = initial
    resolver = MagicMock()
    resolver.resolve.return_value = None
    monkeypatch.setattr(
        native_music,
        "HybridBackend",
        lambda: backend,
    )
    monkeypatch.setattr(music, "CoverArtResolver", lambda: resolver)
    applet = music.MusicApplet(
        48,
        Config(),
        application_registry=MagicMock(),
        application_launcher=MagicMock(),
        platform_services=make_applet_platform(),
    )
    replies = _UIReplies()
    applet._worker = replies.worker
    try:
        applet._tick()
        old_reply = replies.next()
        if action == "next":
            expected = replace(initial, title="Second song")
            backend.poll.return_value = expected
            applet._action_next()
        else:
            expected = replace(initial, volume_percent=50 + VOLUME_STEP)
            backend.poll.return_value = expected
            applet.on_scroll(direction_up=True)
        assert applet._state == expected

        old_reply()

        assert applet._state == expected
        applet._tick()
        replies.next()()
        assert applet._state == expected
    finally:
        applet.stop()


def test_micshield_old_poll_does_not_undo_mute(monkeypatch):
    initial = MicShieldState(available=True, muted=False, active=False)
    probe = MagicMock(return_value=initial)
    monkeypatch.setattr(
        native_micshield,
        "probe_mic_state",
        probe,
    )
    monkeypatch.setattr(
        native_micshield,
        "set_mic_muted",
        lambda **_kwargs: True,
    )
    applet = micshield.MicShieldApplet(
        48, Config(), platform_services=make_applet_platform()
    )
    replies = _UIReplies()
    applet._worker = replies.worker
    try:
        applet._tick()
        old_reply = replies.next()
        expected = replace(initial, muted=True)
        probe.return_value = expected
        applet._set_muted(True)
        replies.next()()  # Mute completion schedules its fresh state read.
        replies.next()()
        assert applet._state == expected

        old_reply()

        assert applet._state == expected
        # A newer pending probe must not starve a completed result.
        probe.return_value = initial
        applet._tick()
        completed_reply = replies.next()
        applet._tick()
        pending_reply = replies.next()
        completed_reply()
        assert applet._state == initial
        pending_reply()
    finally:
        applet.stop()


@pytest.mark.parametrize("action", ["set", "toggle"])
def test_micshield_pre_action_poll_is_rejected_before_fresh_reply(monkeypatch, action):
    initial = MicShieldState(available=True, muted=False, active=False)
    probe = MagicMock(return_value=initial)
    monkeypatch.setattr(
        native_micshield,
        "probe_mic_state",
        probe,
    )
    monkeypatch.setattr(
        native_micshield,
        "set_mic_muted",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        native_micshield,
        "toggle_mic_mute",
        lambda: True,
    )
    applet = micshield.MicShieldApplet(
        48, Config(), platform_services=make_applet_platform()
    )
    replies = _UIReplies()
    applet._worker = replies.worker
    try:
        probe.return_value = replace(initial, active=True)
        applet._tick()
        old_reply = replies.next()
        expected = replace(initial, muted=True)
        probe.return_value = expected
        if action == "set":
            applet._set_muted(True)
        else:
            applet.on_clicked()
        replies.next()()  # Action completed; its fresh probe is still queued.

        old_reply()

        assert applet._state == initial
        replies.next()()
        assert applet._state == expected
    finally:
        applet.stop()


def test_music_scroll_sync_can_refresh_while_old_poll_is_pending(monkeypatch):
    initial = MusicState(available=True, title="First song", volume_percent=50)
    backend = MagicMock()
    backend.poll.return_value = initial
    resolver = MagicMock()
    resolver.resolve.return_value = None
    monkeypatch.setattr(
        native_music,
        "HybridBackend",
        lambda: backend,
    )
    monkeypatch.setattr(music, "CoverArtResolver", lambda: resolver)
    applet = music.MusicApplet(
        48,
        Config(),
        application_registry=MagicMock(),
        application_launcher=MagicMock(),
        platform_services=make_applet_platform(),
    )
    replies = _UIReplies()
    applet._worker = replies.worker
    try:
        applet._tick()
        old_reply = replies.next()
        applet.on_scroll(direction_up=True)
        expected = replace(initial, title="Second song", volume_percent=51)
        backend.poll.return_value = expected
        music.GLib.source_remove(applet._scroll_sync_id)

        applet._run_scroll_sync()
        # Synchronization must not wait for the next periodic poll.
        replies.next()()
        old_reply()

        assert applet._state == expected
    finally:
        applet.stop()


def test_bluetooth_old_poll_does_not_undo_power_off(monkeypatch):
    adapter = BluetoothAdapterState(
        path="/org/bluez/hci0",
        name="Adapter",
        alias="Adapter",
        address="AA:BB:CC:DD:EE:FF",
        powered=True,
        discovering=False,
    )
    initial = BluetoothState(
        available=True,
        adapters=(adapter,),
        devices=(),
        active_adapter_path=adapter.path,
    )
    backend = MagicMock()
    backend.get_state.return_value = initial
    monkeypatch.setattr(
        _native_tests_platform_factories.TestAppletBackendFactories,
        "bluetooth",
        staticmethod(lambda: backend),
    )
    applet = bluetooth.BluetoothApplet(
        48,
        Config(applet_prefs={"bluetooth": {"continuous_discovery": False}}),
        platform_services=make_applet_platform(),
    )
    replies = _UIReplies()
    applet._worker = replies.worker
    try:
        applet._refresh_now()
        replies.next()()
        applet._tick()
        old_reply = replies.next()
        expected = replace(initial, adapters=(replace(adapter, powered=False),))
        backend.get_state.return_value = expected
        applet.on_clicked()
        replies.next()()
        assert applet._state == expected

        old_reply()

        assert applet._state == expected
        applet._tick()
        replies.next()()
        assert applet._state == expected
    finally:
        applet.stop()
