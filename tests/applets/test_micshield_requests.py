"""Microphone IO stays off construction and stale replies cannot restore state."""

from dataclasses import replace

import pytest

from docking.applets.micshield import applet as ui
from docking.applets.micshield.state import MicShieldState
from docking.applets.worker import BackgroundWorker
from docking.core.config import Config


class Backend:
    def __init__(self):
        self.state = MicShieldState(True, False, False)
        self.reads, self.writes = 0, []
        self.closed = False

    def get_state(self):
        assert not self.closed
        self.reads += 1
        return self.state

    def set_muted(self, muted):
        assert not self.closed
        self.writes.append(muted)
        self.state = replace(self.state, muted=muted)
        return True

    def toggle_muted(self):
        return self.set_muted(not self.state.muted)

    def stop(self):
        self.closed = True

    def settings_available(self):
        return False


class Requests:
    def __init__(self):
        self.work, self.replies = [], []
        self.worker = BackgroundWorker(
            idle_add=self.enqueue, thread_factory=self.thread
        )

    def enqueue(self, callback, *args):
        self.replies.append(lambda: callback(*args))

    def thread(self, *, target, daemon):
        owner = self

        class DeferredThread:
            def start(self):
                owner.work.append(target)

        return DeferredThread()


@pytest.fixture
def applet(monkeypatch):
    backend = Backend()
    monkeypatch.setattr(ui, "create_backend", lambda: backend)
    applet = ui.MicShieldApplet(48, Config())
    requests = Requests()
    applet._worker = requests.worker
    yield applet, backend, requests
    applet.stop()


def test_constructor_does_not_query_microphone_provider(applet):
    app, backend, _ = applet
    assert backend.reads == 0
    assert not app._state.available


def test_duplicate_polls_are_guarded_until_the_reply_is_serviced(applet):
    app, backend, requests = applet
    app._refresh_now()
    app._refresh_now()
    assert len(requests.work) == 1 and backend.reads == 0
    requests.work[0]()
    assert backend.reads == 1
    requests.replies[0]()
    assert app._state.available


def test_failed_poll_invalidates_last_confirmed_mute(applet):
    app, backend, requests = applet
    app._on_probe_result(replace(backend.state, muted=True))

    def fail():
        raise RuntimeError("provider failed")

    backend.get_state = fail
    app._refresh_now()
    requests.work[0]()
    requests.replies[0]()
    assert not app._state.available and app._state.error
    assert app._state.muted is None


def test_poll_before_mute_cannot_undo_confirmed_readback(applet):
    app, backend, requests = applet
    app._on_probe_result(backend.state)
    app._refresh_now()
    requests.work[0]()  # Read unmuted, hold its GTK reply.
    app._set_muted(True)
    requests.work[1]()
    requests.replies[1]()
    assert app._state.muted
    requests.replies[0]()
    assert app._state.muted and backend.writes == [True]


@pytest.mark.parametrize("kind", ["poll", "mute"])
def test_queued_work_after_stop_never_reaches_closed_provider(applet, kind):
    app, backend, requests = applet
    app._on_probe_result(backend.state)
    if kind == "poll":
        app._refresh_now()
    else:
        app._set_muted(True)
    app.stop()
    requests.work[0]()
    requests.replies[0]()
    assert backend.reads == 0 and backend.writes == []


def test_late_active_capture_reply_cannot_restart_pulse_after_stop(applet):
    app, backend, requests = applet
    backend.state = replace(backend.state, active=True)
    app._refresh_now()
    requests.work[0]()
    app.stop()
    requests.replies[0]()
    assert not app._state.available and app._pulse_timer_id == 0


def test_action_readback_after_stop_is_not_applied(applet):
    app, backend, requests = applet
    app._on_probe_result(backend.state)
    app._set_muted(True)
    requests.work[0]()
    assert backend.writes == [True] and not app._state.muted
    app.stop()
    requests.replies[0]()
    assert not app._state.muted


def test_only_one_user_transition_runs_at_a_time(applet):
    app, backend, requests = applet
    app._on_probe_result(backend.state)
    app._set_muted(True)
    app._set_muted(False)
    app._refresh_now()
    assert len(requests.work) == 1
    requests.work[0]()
    requests.replies[0]()
    assert app._state.muted and not app._action_in_progress
    app._set_muted(False)
    requests.work[1]()
    requests.replies[1]()
    assert not app._state.muted and backend.writes == [True, False]


def test_restart_replaces_provider_and_rejects_old_queued_action(applet, monkeypatch):
    app, old, requests = applet
    app._on_probe_result(old.state)
    app._set_muted(True)
    app.stop()
    replacement = Backend()
    monkeypatch.setattr(ui, "create_backend", lambda: replacement)
    monkeypatch.setattr(ui.GLib, "idle_add", lambda *args: 1)
    monkeypatch.setattr(ui.GLib, "timeout_add_seconds", lambda *args: 2)
    monkeypatch.setattr(ui.GLib, "source_remove", lambda *args: None)
    app.start(lambda: None)
    requests.work[0]()
    requests.replies[0]()
    assert old.writes == [] and replacement.reads == 0
    app._refresh_now()
    requests.work[1]()
    requests.replies[1]()
    assert app._state.available and not app._state.muted
