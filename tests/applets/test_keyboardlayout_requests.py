"""Input ordering and applet lifetime across asynchronous layout switches."""

from unittest.mock import MagicMock

import pytest

from docking.applets.keyboardlayout.applet import KeyboardLayoutApplet
from docking.applets.keyboardlayout.backend import LayoutBackend
from docking.applets.keyboardlayout.state import LayoutInfo, LayoutState
from docking.core.config import Config
from tests.applets.deferred_worker import DeferredWorker
from tests.applets.test_brightness import _ImmediateWorker


class KeyboardBackend(LayoutBackend):
    name = "fixture"

    def __init__(self):
        self.state = LayoutState(
            "us",
            ["us", "fr", "dvorak"],
            (
                LayoutInfo("us", "EN", "US"),
                LayoutInfo("fr", "FR", "French"),
                LayoutInfo("dvorak", "EN", "US-Dvorak"),
            ),
            "context-a",
        )
        self.switches = []
        self.accept = True
        self.stopped = False

    def is_available(self):
        return True

    def query(self):
        return self.state

    def switch(self, layout_code, *, context=""):
        self.switches.append((layout_code, context))
        if self.accept and context == self.state.context:
            self.state = self.state._replace(active=layout_code)

    def stop(self):
        self.stopped = True


@pytest.fixture
def keyboard(monkeypatch):
    backend = KeyboardBackend()
    monkeypatch.setattr(
        "docking.applets.keyboardlayout.applet.create_backend", lambda: backend
    )
    monkeypatch.setattr(
        "docking.applets.keyboardlayout.applet.BackgroundWorker", _ImmediateWorker
    )
    applet = KeyboardLayoutApplet(48, Config())
    return applet, backend


def test_rapid_layout_scrolls_coalesce_and_keep_the_last_target(keyboard):
    applet, backend = keyboard
    queue = DeferredWorker()
    applet._worker = queue.worker
    for _ in range(5):
        applet.on_clicked()
    queue.drain()
    assert backend.switches == [("fr", "context-a"), ("dvorak", "context-a")]
    assert applet._active == "dvorak"
    assert applet.item.name == "US-Dvorak"


def test_denied_switch_restores_actual_source(keyboard):
    applet, backend = keyboard
    backend.accept = False
    applet.on_clicked()
    assert applet._active == "us" and applet.item.name == "US"


def test_old_poll_cannot_overwrite_newer_selection(keyboard):
    applet, _backend = keyboard
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._tick()
    queue.read()
    applet.on_clicked()
    queue.read()
    queue.publish()
    assert applet._active == "fr"
    queue.publish()
    assert applet._active == "fr"


def test_slow_polls_do_not_overlap_and_stop_invalidates_all_queued_work(keyboard):
    applet, backend = keyboard
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._tick()
    applet._tick()
    assert len(queue.tasks) == 1
    queue.read()
    applet.on_clicked()
    applet.on_clicked()
    applet.stop()
    queue.drain()
    assert backend.stopped and backend.switches == []
    assert applet._pending_layout is None and applet._tick() is False


def test_foreground_change_cancels_remaining_requests_for_old_context(keyboard):
    applet, backend = keyboard
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet.on_clicked()
    applet.on_clicked()
    backend.state = backend.state._replace(context="context-b")
    queue.drain()
    assert backend.switches == [("fr", "context-a")]
    assert applet._pending_layout is None and applet._active == "us"


def test_show_current_layout_and_settings_do_not_launch_after_stop(keyboard):
    applet, backend = keyboard
    queue = DeferredWorker()
    applet._worker = queue.worker
    backend.open_settings = MagicMock()
    backend.show_layout = MagicMock()
    applet._open_settings()
    applet._show_layout()
    applet.stop()
    queue.drain()
    backend.open_settings.assert_not_called()
    backend.show_layout.assert_not_called()


def test_windows_applet_uses_native_state_without_linux_command_fallback(monkeypatch):
    from docking.applets.keyboardlayout import backend, linux, windows
    from tests.applets.test_windows_keyboardlayout import KeyboardAPI

    monkeypatch.setattr(backend.environment, "is_windows", lambda: True)
    monkeypatch.setattr(windows, "_native_api", lambda: KeyboardAPI())
    profiles = MagicMock()
    profiles.catalog.return_value = windows.InputCatalog()
    monkeypatch.setattr(windows, "WindowsInputService", lambda: profiles)
    monkeypatch.setattr(
        linux, "_run", lambda cmd: pytest.fail("Windows invoked a Linux command")
    )
    monkeypatch.setattr(
        "docking.applets.keyboardlayout.applet.BackgroundWorker", _ImmediateWorker
    )
    applet = KeyboardLayoutApplet(48, Config())
    assert isinstance(applet._backend, windows.WindowsKeyboardBackend)
    assert applet.item.name == "US" and applet._last_label == "EN"
    applet.stop()


def test_menu_refreshes_external_layout_and_invalidates_an_older_poll(keyboard):
    applet, backend = keyboard
    queue = DeferredWorker()
    applet._worker = queue.worker
    applet._tick()
    queue.read()
    changed = backend.state._replace(active="fr")
    backend.prepare_interaction = lambda state: changed
    applet.get_menu_items()
    assert applet.item.name == "French" and applet._last_label == "FR"
    queue.publish()
    assert applet._active == "fr" and applet.item.name == "French"
