"""Notification IO remains off GTK; stale state cannot erase observed events."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.notifications import applet as mod
from docking.applets.notifications.state import (
    NotificationEntry,
    NotificationsSnapshot,
    NotificationsState,
)
from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker
from tests.platform_factories import TestAppletBackendFactories, make_applet_platform


@pytest.fixture
def setup(monkeypatch):
    queue, stopped = DeferredWorker(), Mock()
    backend = Mock()
    backend.poll.return_value = NotificationsSnapshot(
        NotificationsState(
            available=True,
            paused_known=True,
            can_pause=True,
        )
    )
    monkeypatch.setattr(
        TestAppletBackendFactories, "notifications", staticmethod(lambda: backend)
    )
    worker = SimpleNamespace(
        run=queue.worker.run, run_guarded=queue.worker.run_guarded, stop=stopped
    )
    monkeypatch.setattr(mod, "SerialWorker", lambda **_kw: worker)
    view = mod.NotificationsApplet(
        48, Config(), platform_services=make_applet_platform()
    )
    yield view, backend, queue, stopped
    view.stop()


def test_constructor_does_not_read_native_services_on_gtk(setup):
    view, backend, queue, _stopped = setup
    backend.poll.assert_not_called()
    queue.read()
    assert not view._state.available
    queue.publish()
    assert view._state.available and not view._loading


def test_stopped_pending_read_cannot_update_presentation(setup):
    view, backend, queue, stopped = setup
    queue.read()
    view.stop()
    view.present = Mock()
    queue.publish()
    view.present.assert_not_called()
    backend.stop.assert_called_once()
    stopped.assert_called_once()


def test_stop_before_io_discards_request_without_reading_backend(setup):
    view, backend, queue, _stopped = setup
    view.stop()
    queue.drain()
    backend.poll.assert_not_called()


def test_failed_initial_read_can_retry(setup):
    view, backend, queue, _stopped = setup
    backend.poll.side_effect = [OSError("not ready"), NotificationsSnapshot()]
    queue.drain()
    assert view._error and not view._loading
    view.on_clicked()
    queue.drain()
    assert not view._error


def test_superseded_state_still_delivers_notification_events(setup):
    view, backend, queue, _stopped = setup
    queue.drain()
    old = NotificationsSnapshot(
        view._state, (NotificationEntry("Fixture", "First", "Body"),)
    )
    backend.poll.return_value = old
    view._tick()
    queue.read()
    view.on_clicked()
    backend.set_paused.assert_not_called()
    queue.publish()
    assert len(view._history) == 1 and not view._state.paused
    backend.poll.return_value = NotificationsSnapshot(
        NotificationsState(
            available=True,
            paused_known=True,
            can_pause=True,
            paused=True,
        )
    )
    queue.drain()
    assert view._state.paused and len(view._history) == 1


def test_rapid_clicks_toggle_the_requested_target(setup):
    view, backend, queue, _stopped = setup
    queue.drain()
    view.on_clicked()
    view.on_clicked()
    backend.set_paused.assert_not_called()
    queue.drain()
    assert [call.args[0] for call in backend.set_paused.call_args_list] == [True, False]
