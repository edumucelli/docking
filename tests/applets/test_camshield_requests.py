"""Cam Shield background work survives slow probes, actions and lifecycle changes."""

from unittest.mock import Mock

import pytest

from docking.applets.camshield import applet as module
from docking.applets.camshield.backend import CamshieldBackend
from docking.applets.camshield.state import CamshieldState
from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker
from tests.platform_factories import TestAppletBackendFactories, make_applet_platform


@pytest.fixture
def camera(monkeypatch):
    queue = DeferredWorker()
    backend = Mock(spec=CamshieldBackend)
    backend.get_state.return_value = CamshieldState(True, False, ("test camera",))
    backend.lock_available.return_value = True
    backend.unlock_available.return_value = True
    monkeypatch.setattr(
        TestAppletBackendFactories, "camshield", staticmethod(lambda: backend)
    )
    monkeypatch.setattr(module, "BackgroundWorker", lambda **_: queue.worker)
    monkeypatch.setattr(module.GLib, "idle_add", lambda *_: 0)
    monkeypatch.setattr(module.GLib, "timeout_add_seconds", lambda *_: 0)
    monkeypatch.setattr(module.GLib, "timeout_add", lambda *_: 0)
    instance = module.CamshieldApplet(
        48, Config(), platform_services=make_applet_platform()
    )
    return instance, backend, queue


def test_constructor_defers_io_and_stop_rejects_queued_reads(camera):
    applet, backend, queue = camera
    backend.get_state.assert_not_called()
    applet.stop()
    queue.drain()
    backend.get_state.assert_not_called()
    backend.stop.assert_called_once()
    assert not applet._state.available
    assert applet._tick() is False and applet._pulse_tick() is False


def test_slow_queries_are_guarded_and_failed_query_is_unknown(camera):
    applet, backend, queue = camera
    for _ in range(4):
        applet._tick()
    assert len(queue.tasks) == 1
    backend.get_state.side_effect = OSError("camera query unavailable")
    queue.drain()
    assert applet._state == CamshieldState(False, None, error=True)
    assert "Camera state unavailable" in applet.item.name


def test_old_probe_cannot_undo_completed_lock_action(camera):
    applet, backend, queue = camera
    queue.read()  # Old idle/unlocked result is waiting for GTK.
    applet._run_action("lock")
    applet._run_action("unlock")  # An in-flight action is serialized.
    applet._tick()
    assert len(queue.tasks) == 1
    locked = CamshieldState(True, False, ("test camera",), locked=True)
    backend.get_state.return_value = locked
    queue.read()
    queue.publish()  # The old unlocked reply must be rejected.
    assert applet._action_in_progress and not applet._state.locked
    queue.publish()
    assert applet._state == locked and not applet._action_in_progress
    backend.lock.assert_called_once()
    backend.unlock.assert_not_called()


def test_stop_rejects_queued_action_and_late_result(camera):
    applet, backend, queue = camera
    queue.read()
    applet._run_action("lock")
    applet.stop()
    queue.drain()
    backend.lock.assert_not_called()
    assert not applet._state.available


def test_restart_replaces_backend_and_ignores_previous_lifecycle(camera, monkeypatch):
    applet, old_backend, queue = camera
    queue.read()
    applet.stop()
    replacement = Mock(spec=CamshieldBackend)
    replacement.get_state.return_value = CamshieldState(True, None, ("replacement",))
    monkeypatch.setattr(
        TestAppletBackendFactories, "camshield", staticmethod(lambda: replacement)
    )
    applet.start(lambda: None)
    queue.publish()
    assert not applet._state.available
    applet._tick()
    queue.drain()
    assert applet._state.devices == ("replacement",)
    old_backend.stop.assert_called_once()
    assert old_backend.get_state.call_count == 1
    applet.stop()
