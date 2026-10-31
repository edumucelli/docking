"""Confirmed state, latest intent and worker-owned release during shutdown."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.caffeine import applet as mod
from docking.applets.caffeine import inhibit
from docking.applets.caffeine.state import CaffeineState
from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker
from tests.applets.test_caffeine import FakeInhibitor


@pytest.fixture
def setup(monkeypatch):
    queue = DeferredWorker()
    monkeypatch.setattr(inhibit, "BackgroundWorker", lambda **_kw: queue.worker)
    timers = SimpleNamespace(
        timeout_add_seconds=Mock(return_value=123), source_remove=Mock()
    )
    monkeypatch.setattr(mod, "GLib", timers)
    backend = FakeInhibitor()
    applet = mod.CaffeineApplet(48, Config(), inhibitor=backend)
    monkeypatch.setattr(applet, "save_prefs", Mock())
    try:
        yield applet, backend, queue, timers
    finally:
        applet.stop()
        queue.drain()


def test_active_only_after_native_success_and_main_thread_delivery(setup):
    applet, backend, queue, _ = setup
    applet.on_clicked()
    assert applet._request_pending and not applet._data.active
    assert backend.acquire_calls == 0 and "Loading..." in applet.item.name
    queue.read()
    assert backend.active and not applet._data.active
    queue.publish()
    assert applet._data.active and not applet._request_pending
    assert "keeping awake" in applet.item.name


def test_rapid_toggle_before_start_only_applies_latest_intent(setup):
    applet, backend, queue, _ = setup
    applet.on_clicked()
    applet.on_clicked()
    assert len(queue.tasks) == 1
    queue.drain()
    assert not applet._data.active and not backend.active
    assert backend.acquire_calls == 0


def test_failed_acquire_does_not_claim_active_and_can_be_retried(setup):
    applet, backend, queue, _ = setup
    original = backend.acquire
    backend.acquire = Mock(return_value=False)
    applet.on_clicked()
    queue.drain()
    assert not applet._data.active and not applet._requested
    assert applet._error and "Unavailable" in applet.item.name
    backend.acquire = original
    applet.on_clicked()
    queue.drain()
    assert applet._data.active and not applet._error


def test_failed_release_keeps_actual_active_state_and_allows_retry(setup):
    applet, backend, queue, _ = setup
    applet.on_clicked()
    queue.drain()
    original = backend.release
    backend.release = Mock()
    applet.on_clicked()
    queue.drain()
    assert applet._data.active and applet._error
    backend.release = original
    applet.on_clicked()
    queue.drain()
    assert not applet._data.active and not applet._error


def test_stop_after_native_acquire_releases_without_delivering_old_gtk_result(setup):
    applet, backend, queue, _ = setup
    applet.on_clicked()
    queue.read()
    assert backend.active and len(queue.idles) == 1
    applet.stop()
    queue.read()
    assert not backend.active
    queue.publish()
    assert not applet._data.active and applet._timer_id == 0
    queue.publish()
    assert not applet._data.active
    calls = backend.acquire_calls
    applet.on_clicked()
    assert backend.acquire_calls == calls and not queue.tasks


def test_stop_during_native_call_cleans_up_on_same_worker_before_any_gtk_delivery(
    setup,
):
    applet, backend, queue, _ = setup
    entered, finish = threading.Event(), threading.Event()
    original = backend.acquire
    threads = []

    def acquire():
        threads.append(threading.get_ident())
        entered.set()
        assert finish.wait(3)
        return original()

    backend.acquire = acquire
    applet.on_clicked()
    worker = threading.Thread(target=queue.read)
    worker.start()
    try:
        assert entered.wait(1)
        applet.stop()
        assert worker.is_alive()
    finally:
        finish.set()
        worker.join(3)
    assert not worker.is_alive() and not backend.active
    assert backend.acquire_calls == 1 and backend.release_calls == 1
    assert threads[0] != threading.get_ident()
    assert not applet._session.pending
    queue.drain()
    assert not applet._data.active


def test_stop_before_worker_start_never_acquires(setup):
    applet, backend, queue, _ = setup
    applet.on_clicked()
    applet.stop()
    queue.drain()
    assert backend.acquire_calls == 0 and not backend.active


def test_restart_rejects_old_result_and_accepts_a_new_user_request(setup):
    applet, backend, queue, _ = setup
    applet.on_clicked()
    queue.read()
    applet.stop()
    applet.start(Mock())
    applet.on_clicked()
    queue.publish()
    assert not applet._data.active
    queue.read()
    queue.publish()
    assert backend.active and applet._data.active


def test_duration_change_during_acquire_starts_countdown_after_confirmation(setup):
    applet, backend, queue, timers = setup
    applet.on_clicked()
    applet._set_duration(30)
    assert applet._timer_id == 0
    queue.drain()
    assert applet._data.remaining == 1800 and applet._timer_id == 123
    applet._set_duration(15)
    assert backend.acquire_calls == 1 and applet._data.remaining == 900
    timers.timeout_add_seconds.assert_called_once()


def test_countdown_releases_lease_then_confirms_off(setup):
    applet, backend, queue, _ = setup
    applet.on_clicked()
    queue.drain()
    applet._data = CaffeineState(active=True, duration_min=1, remaining=1)
    assert applet._tick() is False
    assert applet._data.active and applet._request_pending
    queue.drain()
    assert not backend.active and not applet._data.active
    assert applet._data.remaining == 0 and applet._timer_id == 0


def test_oserror_returns_unavailable_instead_of_wedging_pending_state(setup):
    applet, backend, queue, _ = setup
    backend.acquire = Mock(side_effect=OSError("Power service unavailable"))
    applet.on_clicked()
    queue.drain()
    assert not applet._request_pending and not applet._session.pending
    assert not applet._data.active and applet._error


def test_production_worker_finishes_shutdown_without_gtk_and_is_not_a_daemon(
    monkeypatch,
):
    from docking.applets.worker import BackgroundWorker

    monkeypatch.setattr(
        inhibit,
        "BackgroundWorker",
        lambda **kw: BackgroundWorker(idle_add=lambda *_args: 0, **kw),
    )
    entered, finish, released = threading.Event(), threading.Event(), threading.Event()
    backend = FakeInhibitor()
    original_acquire, original_release = backend.acquire, backend.release
    daemon_flags = []

    def acquire():
        daemon_flags.append(threading.current_thread().daemon)
        entered.set()
        assert finish.wait(3)
        return original_acquire()

    def release():
        original_release()
        released.set()

    backend.acquire, backend.release = acquire, release
    session = inhibit.InhibitionSession(backend, Mock())
    session.request(True)
    try:
        assert entered.wait(1)
        session.stop()
    finally:
        finish.set()
    assert released.wait(2)
    assert daemon_flags == [False] and not backend.active
