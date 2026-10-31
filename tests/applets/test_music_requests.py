"""Media discovery and old replies never block or revive the GTK lifecycle."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.music import applet as mod
from docking.applets.music.state import MusicState
from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker


@pytest.fixture
def setup(monkeypatch):
    queue, stopped = DeferredWorker(), Mock()
    worker = SimpleNamespace(
        run=queue.worker.run, run_guarded=queue.worker.run_guarded, stop=stopped
    )
    backend = Mock()
    backend.poll.return_value = MusicState(
        available=True, title="First", player_id="one"
    )
    backend.application_ids.return_value = ()
    monkeypatch.setattr(mod, "create_backend", lambda: backend)
    monkeypatch.setattr(mod, "SerialWorker", lambda **_kw: worker)
    resolver = Mock()
    resolver.resolve.return_value = None
    monkeypatch.setattr(mod, "CoverArtResolver", lambda: resolver)
    view = mod.MusicApplet(
        48, Config(), application_registry=Mock(), application_launcher=Mock()
    )
    yield view, backend, resolver, queue, stopped
    view.stop()


def test_constructor_defers_discovery_and_artwork_to_its_worker(setup):
    view, backend, resolver, queue, _stopped = setup
    backend.poll.assert_not_called()
    resolver.resolve.assert_not_called()
    assert view._loading
    queue.read()
    backend.poll.assert_called_once()
    assert not view._state.available
    queue.publish()
    assert view._state.title == "First" and not view._loading


def test_completed_read_cannot_update_a_stopped_applet(setup):
    view, backend, _resolver, queue, stopped = setup
    queue.read()
    view.stop()
    view.present = Mock()
    queue.publish()
    assert not view._state.available
    view.present.assert_not_called()
    backend.stop.assert_called_once()
    stopped.assert_called_once()


def test_stop_before_discovery_skips_io_even_when_the_queue_is_drained(setup):
    view, backend, resolver, queue, _stopped = setup
    view.stop()
    queue.drain()
    backend.poll.assert_not_called()
    resolver.resolve.assert_not_called()


def test_discovery_failure_can_retry_without_launching_a_different_application(setup):
    view, backend, _resolver, queue, _stopped = setup
    backend.poll.side_effect = [OSError("Service not ready"), MusicState()]
    queue.read()
    queue.publish()
    assert view._error and "Unavailable" in view.item.name
    view.on_clicked()
    view._application_launcher.launch.assert_not_called()
    queue.read()
    queue.publish()
    assert not view._error and not view._loading
    assert "No active player" in view.item.name
