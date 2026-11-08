"""No stale screenshot UI or delayed capture after removal and restart."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.screenshot import applet as mod
from docking.applets.screenshot.state import ScreenshotImage, ScreenshotResult
from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker
from tests.platform_factories import TestAppletBackendFactories, make_applet_platform


@pytest.fixture
def setup(monkeypatch):
    queue, timers = DeferredWorker(), {}
    backend = SimpleNamespace(
        available=Mock(return_value=True),
        capture=Mock(return_value=ScreenshotResult(True)),
        stop=Mock(),
        select_region=False,
        prepare_capture=Mock(),
    )
    monkeypatch.setattr(
        TestAppletBackendFactories, "screenshot", staticmethod(lambda: backend)
    )
    monkeypatch.setattr(mod, "BackgroundWorker", lambda **_kw: queue.worker)

    def timeout(delay, callback):
        timers[5] = (delay, callback)
        return 5

    monkeypatch.setattr(
        mod,
        "GLib",
        SimpleNamespace(
            timeout_add=timeout, source_remove=lambda value: timers.pop(value, None)
        ),
    )
    view = Mock()
    monkeypatch.setattr(mod, "ScreenshotPreview", view)
    applet = mod.ScreenshotApplet(
        48, Config(), platform_services=make_applet_platform()
    )
    try:
        yield applet, backend, queue, timers, view
    finally:
        applet.stop()
        queue.drain()


def test_constructor_does_not_block_on_tool_discovery(setup):
    applet, backend, queue, _timers, _view = setup
    backend.available.assert_not_called()
    assert not applet._available and applet._loading
    queue.drain()
    assert applet._available and not applet._loading


def test_stopped_worker_never_captures_or_opens_a_view(setup):
    applet, backend, queue, timers, view = setup
    queue.drain()
    applet.on_clicked()
    timers[5][1]()
    applet.stop()
    queue.drain()
    backend.capture.assert_not_called()
    view.assert_not_called()


def test_result_completed_before_stop_cannot_publish_after_stop(setup):
    applet, backend, queue, timers, view = setup
    queue.drain()
    backend.capture.return_value = ScreenshotResult(
        True, ScreenshotImage(bytes((1, 2, 3)), 1, 1)
    )
    applet.on_clicked()
    timers[5][1]()
    queue.read()
    applet.stop()
    queue.publish()
    view.assert_not_called()


def test_restart_ignores_old_discovery_and_uses_new_backend(setup, monkeypatch):
    applet, backend, queue, _timers, _view = setup
    queue.read()
    applet.stop()
    replacement = SimpleNamespace(
        available=Mock(return_value=False), stop=Mock(), select_region=False
    )
    monkeypatch.setattr(
        TestAppletBackendFactories, "screenshot", staticmethod(lambda: replacement)
    )
    applet.start(Mock())
    queue.publish()
    assert not applet._available
    queue.drain()
    assert not applet._available and not applet._loading
    backend.stop.assert_called_once()


def test_failed_capture_reports_unavailable_and_can_be_retried(setup):
    applet, backend, queue, timers, view = setup
    queue.drain()
    backend.capture.side_effect = OSError("Capture denied")
    applet.on_clicked()
    timers[5][1]()
    queue.drain()
    assert applet._error and not applet._pending and "Unavailable" in applet.item.name
    view.assert_not_called()
    backend.capture.side_effect = None
    applet.on_clicked()
    timers[5][1]()
    queue.drain()
    assert not applet._error
