"""Tests for the native WhatsApp Web applet."""

from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from gi.repository import GLib

import docking.applets.whatsapp.applet as whatsapp_applet_mod
import docking.applets.whatsapp.browser as whatsapp_browser_mod
from docking.applets.whatsapp.applet import WhatsAppApplet
from docking.applets.whatsapp.bridge import (
    BRIDGE_SCRIPT,
    BridgeEvent,
    parse_bridge_message,
)
from docking.applets.whatsapp.browser import WhatsAppBrowser
from docking.applets.whatsapp.notifications import DesktopNotifier
from docking.applets.whatsapp.render import render_icon
from docking.applets.whatsapp.state import (
    BrowserPhase,
    NavigationTarget,
    WhatsAppState,
    navigation_target,
    parse_unread_count,
    tooltip_text,
)
from docking.core.config import Config


class _FakeBrowser:
    instances: ClassVar[list[_FakeBrowser]] = []

    def __init__(
        self,
        *,
        on_phase,
        on_title,
        on_badge,
        on_notification,
        on_attention_cleared,
    ) -> None:
        self.on_phase = on_phase
        self.on_title = on_title
        self.on_badge = on_badge
        self.on_notification = on_notification
        self.on_attention_cleared = on_attention_cleared
        self.visible = False
        self.started = 0
        self.stopped = 0
        self.toggled = 0
        self.reloaded = 0
        self.cleared = 0
        self.shown = 0
        self.__class__.instances.append(self)

    def start(self) -> bool:
        self.started += 1
        return True

    def stop(self) -> None:
        self.stopped += 1

    def toggle(self) -> None:
        self.toggled += 1
        self.visible = not self.visible

    def reload(self) -> None:
        self.reloaded += 1

    def clear_session(self, callback) -> None:
        self.cleared += 1
        callback(True, "")

    def show(self) -> None:
        self.shown += 1
        self.visible = True


def _browser(**overrides) -> WhatsAppBrowser:
    callbacks = {
        "on_phase": lambda *_args: None,
        "on_title": lambda _title: None,
        "on_badge": lambda _count, _visible: None,
        "on_notification": lambda: None,
        "on_attention_cleared": lambda: None,
    }
    callbacks.update(overrides)
    return WhatsAppBrowser(**callbacks)


@pytest.fixture
def fake_browser(monkeypatch):
    _FakeBrowser.instances.clear()
    monkeypatch.setattr(WhatsAppApplet, "browser_factory", _FakeBrowser)
    return _FakeBrowser


class TestWhatsAppState:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            (None, 0),
            ("", 0),
            ("WhatsApp", 0),
            ("(1) WhatsApp", 1),
            ("  (27) WhatsApp", 27),
            ("(99+) WhatsApp", 99),
            ("Chat with 123", 0),
            ("Alice (4)", 0),
            ("(+4) WhatsApp", 0),
        ],
    )
    def test_parse_unread_count(self, title, expected):
        assert parse_unread_count(title) == expected

    def test_title_update_replaces_badge_without_losing_phase(self):
        state = WhatsAppState(phase=BrowserPhase.READY, title_unread_count=8)

        updated = state.with_title("(3) WhatsApp")

        assert updated.phase is BrowserPhase.READY
        assert updated.title == "(3) WhatsApp"
        assert updated.unread_count == 3

    def test_api_badge_takes_precedence_over_title_and_notification_fallback(self):
        state = WhatsAppState(title_unread_count=4).with_notification()

        exact = state.with_api_badge(2, visible=True)

        assert exact.unread_count == 2
        assert exact.badge_count == 2
        assert exact.badge_uses_notification_fallback is False

    def test_notification_count_is_used_only_without_an_exact_badge(self):
        state = WhatsAppState().with_notification().with_notification()

        assert state.badge_count == 2
        assert state.badge_uses_notification_fallback is True
        assert state.clear_notification_fallback().badge_count == 0

    def test_notification_after_api_clear_becomes_visible_fallback(self):
        state = WhatsAppState().with_api_badge(0, visible=False)

        updated = state.with_notification()

        assert updated.api_unread_count is None
        assert updated.badge_count == 1
        assert updated.badge_uses_notification_fallback is True

    @pytest.mark.parametrize(
        ("uri", "expected"),
        [
            ("https://web.whatsapp.com/", NavigationTarget.INTERNAL),
            ("https://WEB.WHATSAPP.COM/send", NavigationTarget.INTERNAL),
            ("about:blank", NavigationTarget.INTERNAL),
            ("blob:https://web.whatsapp.com/id", NavigationTarget.INTERNAL),
            ("data:text/plain,hello", NavigationTarget.INTERNAL),
            ("http://web.whatsapp.com/", NavigationTarget.EXTERNAL),
            ("https://web.whatsapp.com.example.test/", NavigationTarget.EXTERNAL),
            ("https://example.test/", NavigationTarget.EXTERNAL),
            ("mailto:person@example.test", NavigationTarget.EXTERNAL),
            ("tel:+123", NavigationTarget.EXTERNAL),
            ("javascript:alert(1)", NavigationTarget.BLOCKED),
            ("file:///etc/passwd", NavigationTarget.BLOCKED),
            (None, NavigationTarget.BLOCKED),
        ],
    )
    def test_navigation_policy(self, uri, expected):
        assert navigation_target(uri) is expected

    def test_tooltips_cover_lifecycle_and_unread_state(self):
        assert "click to open" in tooltip_text(WhatsAppState())
        assert "loading" in tooltip_text(WhatsAppState(phase=BrowserPhase.STARTING))
        assert "not installed" in tooltip_text(
            WhatsAppState(phase=BrowserPhase.UNAVAILABLE)
        )
        assert "offline" in tooltip_text(
            WhatsAppState(phase=BrowserPhase.ERROR, error="offline")
        )
        assert "1 unread message" in tooltip_text(
            WhatsAppState(phase=BrowserPhase.READY, title_unread_count=1)
        )
        assert "4 unread messages" in tooltip_text(
            WhatsAppState(phase=BrowserPhase.READY, title_unread_count=4)
        )
        assert "scan the QR" in tooltip_text(
            WhatsAppState(phase=BrowserPhase.LOGIN_REQUIRED)
        )
        assert "synchronizing" in tooltip_text(
            WhatsAppState(phase=BrowserPhase.SYNCING)
        )
        assert "offline" in tooltip_text(WhatsAppState(phase=BrowserPhase.OFFLINE))
        assert "2 new notifications" in tooltip_text(
            WhatsAppState(
                phase=BrowserPhase.READY,
                notification_count=2,
            )
        )


class TestWhatsAppBridge:
    def test_script_uses_public_browser_surface_only(self):
        assert "navigator.setAppBadge" in BRIDGE_SCRIPT
        assert "navigator.onLine" in BRIDGE_SCRIPT
        assert "window.Store" not in BRIDGE_SCRIPT
        assert "window.require" not in BRIDGE_SCRIPT

    def test_parses_badge_and_status_events(self):
        badge = parse_bridge_message(
            '{"version":1,"kind":"badge","count":7,"visible":true}'
        )
        status = parse_bridge_message(
            '{"version":1,"kind":"status","online":true,"auth":"ready"}'
        )

        assert badge == BridgeEvent(
            kind="badge",
            badge_count=7,
            badge_visible=True,
        )
        assert status == BridgeEvent(kind="status", online=True, auth="ready")

    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "not json",
            '{"version":2,"kind":"status","online":true,"auth":"ready"}',
            '{"version":1,"kind":"badge","count":-1,"visible":true}',
            '{"version":1,"kind":"badge","count":true,"visible":true}',
            '{"version":1,"kind":"status","online":"yes","auth":"ready"}',
            '{"version":1,"kind":"status","online":true,"auth":"chat"}',
        ],
    )
    def test_rejects_invalid_events(self, raw):
        assert parse_bridge_message(raw) is None


class TestWhatsAppApplet:
    def test_browser_starts_after_short_delay(self, monkeypatch, fake_browser):
        callbacks = []
        monkeypatch.setattr(
            whatsapp_applet_mod.GLib,
            "timeout_add_seconds",
            lambda seconds, callback: callbacks.append((seconds, callback)) or 17,
        )
        applet = WhatsAppApplet(icon_size=48, config=Config())

        applet.start(lambda: None)

        assert callbacks[0][0] == whatsapp_applet_mod.STARTUP_DELAY_S
        assert callbacks[0][1]() is False
        assert fake_browser.instances[0].started == 1

    def test_click_cancels_delay_and_toggles_browser(self, monkeypatch, fake_browser):
        removed = []
        monkeypatch.setattr(
            whatsapp_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _callback: 23,
        )
        monkeypatch.setattr(
            whatsapp_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )
        applet = WhatsAppApplet(icon_size=48, config=Config())
        applet.start(lambda: None)

        applet.on_clicked()

        assert removed == [23]
        assert fake_browser.instances[0].toggled == 1

    def test_browser_callbacks_update_tooltip_and_badge(self, fake_browser):
        applet = WhatsAppApplet(icon_size=48, config=Config())
        browser = fake_browser.instances[0]

        browser.on_phase(BrowserPhase.READY, "")
        browser.on_title("(6) WhatsApp")

        assert applet.item.name == "WhatsApp: 6 unread messages"
        assert applet.item.badge_count == 6
        assert applet.item.badge_visible is True

        browser.on_title("WhatsApp")
        assert applet.item.badge_count == 0
        assert applet.item.badge_visible is False

        browser.on_badge(4, True)
        assert applet.item.badge_count == 4
        assert applet.item.badge_visible is True

    def test_notification_fallback_clears_when_window_is_opened(self, fake_browser):
        applet = WhatsAppApplet(icon_size=48, config=Config())
        browser = fake_browser.instances[0]
        browser.on_phase(BrowserPhase.READY, "")

        browser.on_notification()
        browser.on_notification()
        assert applet.item.badge_count == 2
        assert applet.item.name == "WhatsApp: 2 new notifications"

        applet.on_clicked()
        assert applet.item.badge_count == 0

    def test_stop_cancels_pending_start_and_stops_browser(
        self, monkeypatch, fake_browser
    ):
        removed = []
        monkeypatch.setattr(
            whatsapp_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _callback: 31,
        )
        monkeypatch.setattr(
            whatsapp_applet_mod.GLib,
            "source_remove",
            lambda source_id: removed.append(source_id),
        )
        applet = WhatsAppApplet(icon_size=48, config=Config())
        applet.start(lambda: None)

        applet.stop()

        assert removed == [31]
        assert fake_browser.instances[0].stopped == 1


class TestWhatsAppBrowserHelpers:
    @pytest.mark.parametrize(
        "phase",
        list(BrowserPhase),
    )
    def test_icon_renders_for_each_phase(self, phase):
        pixbuf = render_icon(size=48, phase=phase)

        assert pixbuf is not None
        assert pixbuf.get_width() == 48
        assert pixbuf.get_height() == 48

    def test_unique_download_path_sanitizes_and_deduplicates(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            whatsapp_browser_mod.GLib,
            "get_user_special_dir",
            lambda _directory: str(tmp_path),
        )
        (tmp_path / "photo.jpg").touch()

        path = whatsapp_browser_mod._unique_download_path("../../photo.jpg")

        assert path == tmp_path / "photo (1).jpg"

    def test_error_message_is_single_line_and_bounded(self):
        error = "first line\nsecond line" + ("x" * 300)

        message = whatsapp_browser_mod._error_message(error)

        assert message == "first line"
        assert len(message) <= 160

    def test_load_webkit_returns_supported_namespace(self):
        whatsapp_browser_mod.load_webkit.cache_clear()
        if not whatsapp_browser_mod.webkit_available():
            pytest.skip("WebKitGTK is not installed")

        webkit = whatsapp_browser_mod.load_webkit()

        assert webkit.WebView is not None

    def test_policy_ignores_external_navigation_without_user_gesture(self, monkeypatch):
        opened = []
        monkeypatch.setattr(
            whatsapp_browser_mod,
            "_open_external_uri",
            lambda uri: opened.append(uri),
        )
        browser = _browser()
        browser._webkit = SimpleNamespace(
            PolicyDecisionType=SimpleNamespace(RESPONSE=1, NEW_WINDOW_ACTION=2)
        )
        decision = MagicMock()
        decision.get_request.return_value.get_uri.return_value = "https://example.test"
        decision.get_navigation_action.return_value.is_user_gesture.return_value = False

        handled = browser._on_decide_policy(None, decision, 3)

        assert handled is True
        decision.ignore.assert_called_once_with()
        assert opened == []

    def test_policy_opens_user_initiated_external_navigation(self, monkeypatch):
        opened = []
        monkeypatch.setattr(
            whatsapp_browser_mod,
            "_open_external_uri",
            lambda uri: opened.append(uri),
        )
        browser = _browser()
        browser._webkit = SimpleNamespace(
            PolicyDecisionType=SimpleNamespace(RESPONSE=1, NEW_WINDOW_ACTION=2)
        )
        decision = MagicMock()
        decision.get_request.return_value.get_uri.return_value = "https://example.test"
        decision.get_navigation_action.return_value.is_user_gesture.return_value = True

        assert browser._on_decide_policy(None, decision, 3) is True
        assert opened == ["https://example.test"]

    @pytest.mark.parametrize(
        ("event", "phase"),
        [
            (BridgeEvent(kind="status", online=False), BrowserPhase.OFFLINE),
            (
                BridgeEvent(
                    kind="status",
                    online=True,
                    auth="login_required",
                ),
                BrowserPhase.LOGIN_REQUIRED,
            ),
            (
                BridgeEvent(kind="status", online=True, auth="unknown"),
                BrowserPhase.SYNCING,
            ),
            (
                BridgeEvent(kind="status", online=True, auth="ready"),
                BrowserPhase.READY,
            ),
        ],
    )
    def test_status_bridge_maps_to_browser_phase(self, event, phase):
        phases = []
        browser = _browser(on_phase=lambda value, _error: phases.append(value))

        browser._apply_status_event(event)

        assert phases == [phase]

    def test_finished_load_does_not_overwrite_bridge_ready_state(self):
        phases = []
        browser = _browser(on_phase=lambda value, _error: phases.append(value))
        browser._webkit = SimpleNamespace(
            LoadEvent=SimpleNamespace(STARTED=1, FINISHED=2)
        )
        browser._apply_status_event(
            BridgeEvent(kind="status", online=True, auth="ready")
        )
        view = MagicMock()
        view.get_title.return_value = "WhatsApp"

        browser._on_load_changed(view, 2)

        assert browser.phase is BrowserPhase.READY
        assert phases == [BrowserPhase.READY]


class TestDesktopNotifier:
    def test_notification_call_contains_click_action(self):
        bus = MagicMock()
        notification = MagicMock()
        shown = []
        notification.get_title.return_value = "Alice"
        notification.get_body.return_value = "Hello"
        notification.get_tag.return_value = "chat-1"
        notifier = DesktopNotifier(
            on_activate=lambda: None,
            on_shown=lambda: shown.append(True),
        )
        notifier._bus = bus

        assert notifier.show(notification) is True

        args = bus.call.call_args.args
        assert args[3] == "Notify"
        unpacked = args[4].unpack()
        assert unpacked[3:6] == ("Alice", "Hello", ["default", "Open"])
        notification.connect.assert_called_once()
        assert shown == [True]

    def test_default_action_presents_window_and_activates_web_notification(self):
        activated = []
        notification = MagicMock()
        notifier = DesktopNotifier(
            on_activate=lambda: activated.append(True),
            on_shown=lambda: None,
        )
        notifier._notifications[12] = notification

        notifier._on_action_invoked(
            MagicMock(),
            "sender",
            "/path",
            "interface",
            "ActionInvoked",
            GLib.Variant("(us)", (12, "default")),
            None,
        )

        assert activated == [True]
        notification.clicked.assert_called_once_with()

    def test_desktop_dismissal_closes_web_notification(self):
        notification = MagicMock()
        notifier = DesktopNotifier(
            on_activate=lambda: None,
            on_shown=lambda: None,
        )
        notifier._notifications[12] = notification
        notifier._notification_ids[id(notification)] = 12

        notifier._on_notification_closed(
            MagicMock(),
            "sender",
            "/path",
            "interface",
            "NotificationClosed",
            GLib.Variant("(uu)", (12, 2)),
            None,
        )

        notification.close.assert_called_once_with()
        assert notifier._notifications == {}
