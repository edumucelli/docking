"""Native browser origin policy, asynchronous requests and stale lifetimes."""

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from docking.applets.telegram.state import PROFILE as TELEGRAM
from docking.applets.webapp.bridge import BridgeEvent, parse_bridge_message
from docking.applets.webapp.implementations.windows import backend as mod
from docking.applets.webapp.state import BrowserPhase
from docking.applets.whatsapp.state import PROFILE as WHATSAPP
from tests.applets.deferred_worker import DeferredWorker


@dataclass
class Engine:
    core: object = "core"

    def __post_init__(self):
        self.writes = []
        self.clears = []
        self.navigations = []
        self.closed = False

    def text(self, args, index, *, limit=32768):
        text = args[index]
        if len(text) > limit:
            raise OSError("Text limit")
        return text

    def put(self, args, index, value):
        self.writes.append((args, index, value))

    def clear(self, callback):
        self.clears.append(callback)

    def navigate(self, uri):
        self.navigations.append(uri)

    def close(self):
        self.closed = True


@pytest.fixture(params=[WHATSAPP, TELEGRAM], ids=["whatsapp", "telegram"])
def setup(monkeypatch, request):
    queue = DeferredWorker()
    monkeypatch.setattr(mod.GLib, "idle_add", queue._idle)
    monkeypatch.setattr(mod.GLib, "source_remove", Mock())
    monkeypatch.setattr(mod, "BackgroundWorker", lambda: queue.worker)
    monkeypatch.setattr(
        mod, "value", lambda args, index, _kind: SimpleNamespace(value=args[index])
    )
    callbacks = {
        name: Mock()
        for name in (
            "on_phase",
            "on_title",
            "on_badge",
            "on_notification",
            "on_attention_cleared",
        )
    }
    browser = mod.WindowsWebAppBrowser(profile=request.param, **callbacks)
    yield browser, callbacks, queue
    browser.stop()


@pytest.mark.parametrize(
    "uri",
    [
        "https://web.whatsapp.com/",
        "https://web.whatsapp.com:443/path",
        "https://WEB.WHATSAPP.COM/",
    ],
)
def test_actual_service_origin(uri):
    assert WHATSAPP.allows_origin(uri)


@pytest.mark.parametrize(
    "uri",
    [
        "http://web.whatsapp.com/",
        "https://web.whatsapp.com.evil.test/",
        "https://evil.test/?next=https://web.whatsapp.com/",
        "https://web.whatsapp.com:8443/",
        "https://user:secret@web.whatsapp.com/",
        "about:blank",
        "data:text/html,fixture",
        "https://web.whatsapp.com:invalid/",
        "https://[broken/",
    ],
)
def test_other_origin_never_gets_service_permissions_or_messages(uri):
    assert not WHATSAPP.allows_origin(uri)


def test_profile_work_and_native_runtime_are_deferred(setup, monkeypatch):
    browser, _callbacks, queue = setup
    prepare, engine = Mock(), Mock()
    monkeypatch.setattr(mod, "ensure_dir", prepare)
    monkeypatch.setattr(mod, "WebView2Host", engine)
    assert browser.start()
    prepare.assert_not_called()
    engine.assert_not_called()
    browser.stop()
    queue.drain()
    engine.assert_not_called()
    assert browser.phase is BrowserPhase.STOPPED


def test_late_profile_result_never_creates_a_window(setup, monkeypatch, tmp_path):
    browser, _callbacks, _queue = setup
    browser._started = True
    browser.stop()
    create = Mock()
    monkeypatch.setattr(mod.Gtk, "Window", create)
    browser._prepared(tmp_path, 0)
    create.assert_not_called()


def test_shared_bridge_activation_is_schema_checked():
    assert parse_bridge_message('{"version":1,"kind":"activate"}') == BridgeEvent(
        kind="activate"
    )
    assert parse_bridge_message("\ud800") is None


def test_untrusted_notification_is_suppressed_without_counting_activity(setup):
    browser, callbacks, _queue = setup
    engine = browser._engine = Engine()
    args = {3: "https://example.test/"}
    browser._notification(args)
    assert engine.writes == [(args, 5, True)]
    callbacks["on_notification"].assert_not_called()


def test_trusted_notification_uses_native_toast_adapter(setup):
    browser, callbacks, _queue = setup
    engine = browser._engine = Engine()
    notifier = browser._notifier = Mock()
    args = {3: browser.profile.origin}
    browser._notification(args)
    notifier.show.assert_called_once_with((engine, args))
    assert engine.writes == []
    callbacks["on_notification"].assert_not_called()


def test_unknown_permission_is_denied_and_never_opens_a_dialog(setup):
    browser, _callbacks, _queue = setup
    engine = browser._engine = Engine()
    args = {3: browser.profile.origin, 4: 999}
    browser._permission(args)
    assert engine.writes == [(args, 7, 2)]
    assert browser._permission_requests == {}


def test_notification_permission_is_granted_only_to_service_origin(setup):
    browser, _callbacks, _queue = setup
    engine = browser._engine = Engine()
    trusted = {3: browser.profile.origin, 4: 4}
    other = {3: "https://example.test/", 4: 4}
    browser._permission(trusted)
    browser._permission(other)
    assert engine.writes == [(trusted, 7, 2), (trusted, 7, 1), (other, 7, 2)]


def test_oversized_invalid_or_other_origin_traffic_does_not_restart_browser(setup):
    browser, callbacks, _queue = setup
    browser._engine = Engine()
    browser._phase = BrowserPhase.READY
    for source, text in [
        (browser.profile.origin, "x" * 5000),
        (
            browser.profile.origin,
            '{"version":1,"kind":"badge","count":true,"visible":true}',
        ),
        (
            "https://example.test/",
            '{"version":1,"kind":"badge","count":10,"visible":true}',
        ),
    ]:
        browser._message({3: source, 5: text})
    assert browser.phase is BrowserPhase.READY and browser._recovery_id == 0
    callbacks["on_badge"].assert_not_called()


def test_external_navigation_requires_a_real_user_gesture(setup, monkeypatch):
    browser, _callbacks, _queue = setup
    engine = browser._engine = Engine()
    pending = []
    monkeypatch.setattr(mod.GLib, "idle_add", lambda *args: pending.append(args))
    browser._phase = BrowserPhase.READY
    silent = {3: "https://example.test/", 4: False, 9: 1}
    gesture = {3: "https://example.test/", 4: True, 9: 2}
    browser._navigation_starting(silent)
    browser._navigation_starting(gesture)
    assert engine.writes == [(silent, 8, True), (gesture, 8, True)]
    assert pending == [(browser._open_external, "https://example.test/")]
    browser._navigation_completed({3: False, 5: 1})
    assert browser.phase is BrowserPhase.READY


def test_canceled_download_navigation_keeps_ready_state(setup):
    browser, _callbacks, _queue = setup
    browser._engine = Engine()
    browser._phase = BrowserPhase.READY
    browser._navigation_completed({3: False, 4: 14, 5: 1})
    assert browser.phase is BrowserPhase.READY


def test_disconnect_waits_for_blank_document_before_clearing_and_ignores_duplicate_load(
    setup,
):
    browser, _callbacks, queue = setup
    engine = browser._engine = Engine(core={4: "about:blank"})
    browser._ready = True
    callback = Mock()
    browser.clear_session(callback)
    assert engine.navigations == ["about:blank"] and engine.clears == []
    browser._navigation_completed({3: True, 5: 1})
    browser._navigation_completed({3: True, 5: 2})
    assert len(engine.clears) == 1
    engine.clears[0](0)
    callback.assert_not_called()
    queue.drain()
    assert engine.navigations[-1] == browser.profile.url
    callback.assert_called_once_with(True, "")


def test_late_native_activation_cannot_reopen_stopped_browser(setup, monkeypatch):
    browser, _callbacks, _queue = setup
    pending = []
    monkeypatch.setattr(mod.GLib, "idle_add", lambda callback: pending.append(callback))
    browser._started = True
    browser._activate_notification()
    browser.stop()
    browser.show = Mock()
    pending.pop()()
    browser.show.assert_not_called()


def test_stop_during_clear_does_not_block_a_new_lifetime(setup):
    browser, _callbacks, _queue = setup
    browser._engine = Engine()
    browser._clearing = True
    browser.stop()
    engine = browser._engine = Engine(core={4: "about:blank"})
    browser._ready = True
    browser.clear_session(Mock())
    browser._navigation_completed({3: True, 5: 10})
    assert len(engine.clears) == 1


def test_failed_permission_cleanup_still_closes_the_controller(setup):
    browser, _callbacks, _queue = setup
    engine = browser._engine = Engine()
    browser._permission_requests[1] = Mock(side_effect=OSError("Browser closed"))
    browser.stop()
    assert engine.closed and browser._engine is None


def test_profiles_prepare_separate_persistent_directories(monkeypatch, tmp_path):
    queue = DeferredWorker()
    monkeypatch.setattr(mod, "BackgroundWorker", lambda: queue.worker)
    monkeypatch.setattr(mod, "docking_data_dir", lambda: tmp_path)
    prepared = []
    browsers = []
    for profile in (WHATSAPP, TELEGRAM):
        browser = mod.WindowsWebAppBrowser(
            profile=profile,
            **{
                name: Mock()
                for name in (
                    "on_phase",
                    "on_title",
                    "on_badge",
                    "on_notification",
                    "on_attention_cleared",
                )
            },
        )
        browser._prepared = lambda path, _lifetime: prepared.append(path)
        browsers.append(browser)
        browser.start()
    queue.drain()
    assert prepared == [
        tmp_path / "whatsapp" / "webview2",
        tmp_path / "telegram" / "webview2",
    ]
    assert all(path.is_dir() for path in prepared)
    for browser in browsers:
        browser.stop()


def test_profile_failure_finishes_disconnect_and_can_be_retried(setup, monkeypatch):
    browser, _callbacks, queue = setup
    monkeypatch.setattr(mod, "ensure_dir", Mock(side_effect=PermissionError()))
    complete = Mock()
    browser.clear_session(complete)
    queue.drain()
    complete.assert_called_once_with(False, "Could not open browser session")
    assert browser._clear_pending is None and not browser._preparing
    assert browser.phase is BrowserPhase.ERROR


def test_stale_profile_failure_does_not_finish_a_new_disconnect(setup):
    browser, _callbacks, _queue = setup
    browser.start()
    stale = browser._lifetime
    browser.stop()
    complete = Mock()
    browser.clear_session(complete)
    browser._prepare_failed(OSError(), stale)
    complete.assert_not_called()


def test_native_clear_rejection_finishes_the_request(setup):
    browser, _callbacks, queue = setup
    engine = browser._engine = Engine(core={4: "about:blank"})
    engine.clear = Mock(side_effect=OSError())
    browser._ready = True
    complete = Mock()
    browser.clear_session(complete)
    browser._navigation_completed({3: True, 5: 1})
    queue.drain()
    complete.assert_called_once_with(
        False, f"Could not disconnect {browser.profile.name}"
    )
    assert not browser._clearing


def test_old_native_clear_reply_cannot_complete_a_new_request(setup, monkeypatch):
    browser, _callbacks, queue = setup
    engine = browser._engine = Engine(core={4: "about:blank"})
    browser._ready = True
    first, second = Mock(), Mock()
    browser.clear_session(first)
    browser._navigation_completed({3: True, 5: 1})
    browser._fail_clear("Browser stopped")
    browser.clear_session(second)
    engine.clears[0](0)
    queue.drain()
    first.assert_called_once_with(False, "Browser stopped")
    second.assert_not_called()
    assert browser._clear_pending is second


def test_unavailable_runtime_finishes_disconnect_and_destroys_window(
    setup, monkeypatch, tmp_path
):
    browser, _callbacks, queue = setup
    window = SimpleNamespace(set_title=Mock(), destroy=Mock())
    monkeypatch.setattr(mod.Gtk, "Window", lambda **_kwargs: window)
    monkeypatch.setattr(mod.Gdk.Display, "get_default", lambda: None)
    complete = Mock()
    browser.clear_session(complete)
    browser._prepared(tmp_path, browser._lifetime)
    queue.publish()
    complete.assert_called_once_with(False, "Microsoft Edge WebView2 is unavailable")
    window.destroy.assert_called_once()
    assert browser._window is None and browser.phase is BrowserPhase.UNAVAILABLE


def test_stop_drops_a_queued_disconnect_dialog_callback(setup):
    browser, _callbacks, queue = setup
    browser._engine = Engine()
    complete = Mock()
    browser.clear_session(complete)
    browser._fail_clear("Browser stopped")
    complete.assert_not_called()
    browser.stop()
    queue.drain()
    complete.assert_not_called()
