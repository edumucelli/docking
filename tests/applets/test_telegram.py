"""Telegram metadata, independent sessions, and shared-browser regressions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from docking.applets import get_applet_catalog, load_applet_class
from docking.applets.telegram.applet import TelegramApplet
from docking.applets.telegram.bridge import BRIDGE_HANDLER, BRIDGE_SCRIPT
from docking.applets.telegram.browser import PROFILE, TelegramBrowser
from docking.applets.telegram.render import render_icon
from docking.applets.telegram.state import (
    TELEGRAM_WEB_URL,
    TelegramState,
    navigation_target,
    parse_unread_count,
    tooltip_text,
)
from docking.applets.webapp.bridge import parse_bridge_message
from docking.applets.webapp.browser import _session_directories
from docking.applets.webapp.notifications import DesktopNotifier
from docking.applets.webapp.state import BrowserPhase, NavigationTarget
from docking.applets.whatsapp.browser import PROFILE as WHATSAPP_PROFILE
from docking.core.config import Config


def browser(**overrides):
    callbacks = {
        "on_phase": lambda *_args: None,
        "on_title": lambda _title: None,
        "on_badge": lambda _count, _visible: None,
        "on_notification": lambda: None,
        "on_attention_cleared": lambda: None,
    }
    callbacks.update(overrides)
    return TelegramBrowser(**callbacks)


@pytest.fixture
def applet(monkeypatch):
    fake = SimpleNamespace(
        visible=False,
        start=MagicMock(),
        stop=MagicMock(),
        reload=MagicMock(),
        show=MagicMock(),
        clear_session=MagicMock(),
    )

    def toggle():
        fake.visible = not fake.visible

    fake.toggle = toggle

    def factory(**callbacks):
        for name, callback in callbacks.items():
            setattr(fake, name, callback)
        return fake

    monkeypatch.setattr(TelegramApplet, "browser_factory", staticmethod(factory))
    instance = TelegramApplet(48, Config())
    yield instance, fake
    instance.stop()


def test_catalog_loads_telegram_with_its_own_metadata():
    assert get_applet_catalog()["telegram"].name == "Telegram"
    assert load_applet_class("telegram") is TelegramApplet


@pytest.mark.parametrize(
    "title,count",
    [
        ("Telegram Web", 0),
        ("(4) Telegram", 4),
        ("Telegram (7)", 7),
        ("Telegram Web (99+)", 99),
        ("Project 2026", 0),
        (None, 0),
        ("Telegram — chat 15", 0),
        ("(3+) Telegram", 3),
    ],
)
def test_title_parser_only_accepts_an_explicit_count_marker(title, count):
    assert parse_unread_count(title) == count


def test_public_api_chat_count_has_priority_over_blinking_titles():
    state = TelegramState(phase=BrowserPhase.READY)
    state = state.with_api_badge(4, visible=True).with_title("Telegram Web")
    assert isinstance(state, TelegramState)
    assert state.badge_count == 4
    assert tooltip_text(state) == "Telegram: 4 unread chats"
    assert state.with_title("(9) Telegram").badge_count == 4
    assert state.with_api_badge(0, visible=False).badge_count == 0


def test_notification_fallback_is_named_and_cleared_separately_from_exact_count():
    state = TelegramState(phase=BrowserPhase.READY).with_notification()
    assert tooltip_text(state) == "Telegram: 1 new notification"
    assert state.clear_notification_fallback().badge_count == 0
    exact = state.with_api_badge(3, visible=True)
    assert exact.clear_notification_fallback().badge_count == 3


def test_browser_profiles_separate_origin_bridge_and_persistent_data(
    tmp_path, monkeypatch
):
    import docking.applets.webapp.browser as shared

    monkeypatch.setattr(shared, "docking_data_dir", lambda: tmp_path / "data")
    monkeypatch.setattr(shared, "docking_cache_dir", lambda: tmp_path / "cache")
    tg_data, tg_cache = _session_directories(PROFILE.session_name)
    wa_data, wa_cache = _session_directories(WHATSAPP_PROFILE.session_name)
    assert tg_data != wa_data and tg_cache != wa_cache
    assert PROFILE.origin == "https://web.telegram.org/"
    assert PROFILE.bridge_handler != WHATSAPP_PROFILE.bridge_handler
    assert tg_data.stat().st_mode & 0o777 == 0o700
    assert tg_cache.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "uri,target",
    [
        (TELEGRAM_WEB_URL, NavigationTarget.INTERNAL),
        ("https://web.telegram.org/a/", NavigationTarget.INTERNAL),
        ("https://web.telegram.org:444/", NavigationTarget.EXTERNAL),
        ("https://user@web.telegram.org/k/", NavigationTarget.EXTERNAL),
        ("https://web.telegram.org.evil.test/", NavigationTarget.EXTERNAL),
        ("https://web.whatsapp.com/", NavigationTarget.EXTERNAL),
        ("javascript:alert(1)", NavigationTarget.BLOCKED),
        ("https://[bad", NavigationTarget.BLOCKED),
        ("https://web.telegram.org:invalid/", NavigationTarget.BLOCKED),
    ],
)
def test_navigation_stays_within_the_telegram_origin(uri, target):
    assert navigation_target(uri) is target


def test_bridge_only_observes_public_badges_and_visible_page_landmarks():
    assert BRIDGE_HANDLER in BRIDGE_SCRIPT
    assert "navigator.setAppBadge" in BRIDGE_SCRIPT
    assert "getClientRects" in BRIDGE_SCRIPT
    assert "#auth-pages" in BRIDGE_SCRIPT
    assert "window.Store" not in BRIDGE_SCRIPT
    assert "rootScope" not in BRIDGE_SCRIPT
    assert "innerText" not in BRIDGE_SCRIPT
    assert "textContent" not in BRIDGE_SCRIPT
    assert parse_bridge_message("\ud800") is None


def test_browser_bridge_updates_are_validated_and_deduplicated():
    updates = []
    instance = browser(on_badge=lambda count, visible: updates.append((count, visible)))
    message = MagicMock()
    message.get_js_value.return_value.to_string.return_value = json.dumps(
        {
            "version": 1,
            "kind": "badge",
            "count": 3,
            "visible": True,
        }
    )
    instance._on_script_message(None, message)
    instance._on_script_message(None, message)
    assert updates == [(3, True)]


def test_ui_uses_telegram_labels_and_exact_badges(applet):
    instance, fake = applet
    fake.on_phase(BrowserPhase.READY, "")
    fake.on_badge(2, True)
    assert instance.item.badge_count == 2
    assert instance.item.name == "Telegram: 2 unread chats"
    labels = [item.get_label() for item in instance.get_menu_items()]
    assert "Open Telegram" in labels and "Disconnect Telegram" in labels
    assert not any("WhatsApp" in label for label in labels)
    instance.on_clicked()
    assert fake.visible
    assert instance.item.badge_count == 2


def test_open_in_browser_uses_the_official_telegram_web_url(applet, monkeypatch):
    import docking.applets.webapp.applet as shared

    opened = []
    monkeypatch.setattr(shared.targets, "open_target", lambda uri: opened.append(uri))
    applet[0]._open_in_browser()
    assert opened == [TELEGRAM_WEB_URL]


def test_notification_identity_and_fallback_title_are_provider_specific():
    notifier = DesktopNotifier(
        service_name="Telegram", on_activate=lambda: None, on_shown=lambda: None
    )
    notifier._bus = MagicMock()
    notification = MagicMock()
    notification.get_title.return_value = ""
    notification.get_body.return_value = "Synthetic notification"
    notification.get_tag.return_value = "test"
    assert notifier.show(notification)
    payload = notifier._bus.call.call_args.args[4].unpack()
    assert payload[0] == "Telegram in Docking"
    assert payload[3] == "Telegram"


@pytest.mark.parametrize("action", ["clear", "stop"])
def test_late_notification_reply_closes_popup_using_its_original_connection(action):
    notifier = DesktopNotifier(
        service_name="Telegram", on_activate=lambda: None, on_shown=lambda: None
    )
    connection = MagicMock()
    connection.call_finish.return_value.unpack.return_value = (42,)
    notifier._bus = connection
    notification = MagicMock()
    notification.get_tag.return_value = "late-notification"
    assert notifier.show(notification)
    callback_data = connection.call.call_args.args[-1]

    getattr(notifier, action)()
    notifier._on_notify_finished(connection, MagicMock(), callback_data)

    call = connection.call.call_args.args
    assert call[3] == "CloseNotification"
    assert call[4].unpack() == (42,)
    notification.close.assert_called_once_with()
    assert notifier._notifications == {}
    if action == "stop":
        assert notifier._bus is None


def test_stopped_browser_ignores_a_late_session_clear_completion():
    instance = browser()
    instance._webkit = SimpleNamespace(WebsiteDataTypes=SimpleNamespace(ALL=1))
    manager = MagicMock()
    instance._manager = manager
    instance._view = MagicMock()
    instance._window = MagicMock()
    instance._started = True
    completed = MagicMock()
    instance.clear_session(completed)
    callback_data = manager.clear.call_args.args[-1]
    cancellable = manager.clear.call_args.args[2]
    instance.stop()
    instance._on_session_cleared(manager, MagicMock(), callback_data)
    assert cancellable.is_cancelled()
    assert instance.phase is BrowserPhase.STOPPED
    completed.assert_not_called()
    assert instance._window is None


def test_stopped_applet_cannot_be_resurrected_by_a_late_clear_callback(applet):
    instance, fake = applet
    instance.stop()
    instance._on_session_cleared(True, "")
    fake.show.assert_not_called()


@pytest.mark.parametrize(
    "permission_name", ["GeolocationPermissionRequest", "UserMediaPermissionRequest"]
)
def test_sensitive_or_unknown_permissions_are_not_automatically_allowed(
    permission_name,
):
    request = type(permission_name, (), {})()
    request.allow = MagicMock()
    request.deny = MagicMock()
    instance = browser()
    instance._confirm_media_permission = MagicMock(return_value=False)
    assert instance._on_permission_request(None, request)
    request.allow.assert_not_called()
    request.deny.assert_called_once_with()


@pytest.mark.parametrize(
    "permission_name",
    ["NotificationPermissionRequest", "WebsiteDataAccessPermissionRequest"],
)
def test_expected_web_session_permissions_are_allowed(permission_name):
    request = type(permission_name, (), {})()
    request.allow = MagicMock()
    request.deny = MagicMock()
    assert browser()._on_permission_request(None, request)
    request.allow.assert_called_once_with()
    request.deny.assert_not_called()


@pytest.mark.parametrize("phase", list(BrowserPhase))
def test_icon_renders_in_each_connection_state(phase):
    icon = render_icon(size=48, phase=phase)
    assert icon is not None and icon.get_width() == icon.get_height() == 48
