"""Tests for notifications applet and backend helpers."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import docking.applets.notifications.applet as notifications_applet_mod
import docking.applets.notifications.state as notifications_state_mod
from docking.applets.notifications import (
    DunstBackend,
    GnomeBackend,
    NotificationsApplet,
    NotificationsState,
    NullBackend,
    detect_backend,
    tooltip_text,
    unavailable_state,
)
from docking.applets.notifications.applet import HISTORY_LIMIT, NotificationEntry
from docking.applets.notifications.render import create_notifications_icon


def _state(**overrides: object) -> NotificationsState:
    base = NotificationsState(
        available=True,
        backend="dunstctl",
        paused=False,
        pending=3,
        pending_known=True,
    )
    values = {field: getattr(base, field) for field in NotificationsState.__dataclass_fields__}
    values.update(overrides)
    return NotificationsState(**values)


class TestTooltipText:
    def test_unavailable(self):
        assert "No backend" in tooltip_text(unavailable_state())

    def test_contains_pending(self):
        text = tooltip_text(_state(paused=True, pending=5, pending_known=True))
        assert "Pending: 5" in text

    def test_hides_pending_when_unknown(self):
        text = tooltip_text(_state(pending_known=False))
        assert "Pending" not in text
        assert text == "Notifications"


class TestStateParsing:
    def test_parse_bool_values(self):
        assert notifications_state_mod._parse_bool("true") is True
        assert notifications_state_mod._parse_bool("false") is False
        assert notifications_state_mod._parse_bool("invalid") is None

    def test_parse_pending_count_values(self):
        assert notifications_state_mod._parse_pending_count("7") == 7
        assert (
            notifications_state_mod._parse_pending_count(
                '{"displayed": 0, "history": 2, "waiting": 4}'
            )
            == 4
        )
        assert notifications_state_mod._parse_pending_count("oops") is None

    def test_dunst_backend_get_state(self, monkeypatch):
        def fake_run(cmd: list[str], timeout_s: float = 2.0) -> str | None:
            _ = timeout_s
            table = {
                ("dunstctl", "is-paused"): "false",
                ("dunstctl", "count", "waiting"): "6",
            }
            return table.get(tuple(cmd))

        monkeypatch.setattr(notifications_state_mod, "_run", fake_run)
        state = DunstBackend().get_state()
        assert state.available is True
        assert state.paused is False
        assert state.pending == 6
        assert state.pending_known is True

    def test_dunst_backend_falls_back_to_json_count(self, monkeypatch):
        def fake_run(cmd: list[str], timeout_s: float = 2.0) -> str | None:
            _ = timeout_s
            table = {
                ("dunstctl", "is-paused"): "true",
                ("dunstctl", "count", "waiting"): None,
                ("dunstctl", "count"): '{"displayed": 0, "history": 3, "waiting": 2}',
            }
            return table.get(tuple(cmd))

        monkeypatch.setattr(notifications_state_mod, "_run", fake_run)
        state = DunstBackend().get_state()
        assert state.available is True
        assert state.paused is True
        assert state.pending == 2
        assert state.pending_known is True

    def test_gnome_backend_get_state(self, monkeypatch):
        monkeypatch.setattr(
            notifications_state_mod,
            "_run",
            lambda cmd, timeout_s=2.0: "false"
            if cmd[:3] == ["gsettings", "get", "org.gnome.desktop.notifications"]
            else None,
        )
        state = GnomeBackend().get_state()
        assert state.available is True
        assert state.paused is True
        assert state.pending_known is False


class TestBackendDetection:
    def test_detect_backend_prefers_dunst(self, monkeypatch):
        monkeypatch.setattr(
            notifications_state_mod,
            "_has_command",
            lambda cmd: cmd in {"dunstctl", "gsettings"},
        )
        monkeypatch.setattr(
            notifications_state_mod.DunstBackend,
            "get_state",
            lambda self: _state(),
        )
        backend = detect_backend()
        assert isinstance(backend, DunstBackend)

    def test_detect_backend_falls_back_to_gnome(self, monkeypatch):
        monkeypatch.setattr(notifications_state_mod, "_has_command", lambda cmd: True)
        monkeypatch.setattr(
            notifications_state_mod.DunstBackend,
            "get_state",
            lambda self: unavailable_state(),
        )
        monkeypatch.setattr(
            notifications_state_mod.GnomeBackend,
            "get_state",
            lambda self: _state(backend="gnome", pending_known=False),
        )
        backend = detect_backend()
        assert isinstance(backend, GnomeBackend)

    def test_detect_backend_returns_null_when_none_available(self, monkeypatch):
        monkeypatch.setattr(notifications_state_mod, "_has_command", lambda cmd: False)
        backend = detect_backend()
        assert isinstance(backend, NullBackend)


class _StubBackend:
    def __init__(self, state: NotificationsState, supports_clear: bool = True) -> None:
        self._state = state
        self.supports_clear = supports_clear
        self.set_paused_calls: list[bool] = []
        self.clear_calls = 0

    def get_state(self) -> NotificationsState:
        return self._state

    def set_paused(self, paused: bool) -> bool:
        self.set_paused_calls.append(paused)
        self._state = replace(self._state, paused=paused)
        return True

    def clear_notifications(self) -> bool:
        self.clear_calls += 1
        self._state = replace(self._state, pending=0)
        return True


def _make_applet(
    monkeypatch,
    state: NotificationsState,
    supports_clear: bool = True,
) -> tuple[NotificationsApplet, _StubBackend]:
    backend = _StubBackend(state=state, supports_clear=supports_clear)
    monkeypatch.setattr(notifications_applet_mod, "detect_backend", lambda: backend)
    return NotificationsApplet(48), backend


class TestNotificationsApplet:
    def test_creates_with_icon(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        assert applet.item.icon is not None

    def test_click_toggles_dnd(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state(paused=False))
        applet.on_clicked()
        assert backend.set_paused_calls == [True]
        assert applet._state.paused is True

    def test_unavailable_menu(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, unavailable_state())
        items = applet.get_menu_items()
        assert len(items) == 1
        assert "No notification backend" in items[0].get_label()
        assert items[0].get_sensitive() is False

    def test_menu_includes_pending_and_clear(self, monkeypatch):
        applet, _backend = _make_applet(
            monkeypatch,
            _state(pending=8, pending_known=True),
            supports_clear=True,
        )
        labels = [item.get_label() for item in applet.get_menu_items() if item.get_label()]
        assert "Do Not Disturb" in labels
        assert "Pending: 8" in labels
        assert "Clear History" in labels
        assert "Clear Notifications" in labels

    def test_clear_updates_pending(self, monkeypatch):
        applet, backend = _make_applet(monkeypatch, _state(pending=5))
        applet._on_clear()
        assert backend.clear_calls == 1
        assert applet._state.pending == 0

    def test_clear_history_clears_stored_notifications(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._history = [
            NotificationEntry(app_name="Mail", summary="A", body="B"),
            NotificationEntry(app_name="Chat", summary="C", body="D"),
        ]
        applet._history_index = 1
        applet.refresh_presentation = MagicMock()

        applet._on_clear_history()

        assert applet._history == []
        assert applet._history_index == 0
        applet.refresh_presentation.assert_called_once()

    def test_poll_result_refreshes_only_on_change(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending=1))
        applet.refresh_presentation = MagicMock()
        assert applet._on_poll_result(_state(pending=1)) is False
        applet.refresh_presentation.assert_not_called()
        assert applet._on_poll_result(_state(pending=2)) is False
        applet.refresh_presentation.assert_called_once()

    def test_activity_badge_shows_for_unknown_pending(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        applet._activity_until_monotonic = float("inf")
        assert applet._show_activity_badge() is True

    def test_activity_badge_hidden_when_pending_known(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=True))
        applet._activity_until_monotonic = float("inf")
        assert applet._show_activity_badge() is False

    def test_history_badge_count_uses_history_size(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._history = [NotificationEntry("a", "b", "c")] * 4
        assert applet._history_badge_count() == 4

    def test_create_icon_passes_history_badge_count(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state())
        applet._history = [NotificationEntry("a", "b", "c")] * 3
        captured: dict[str, object] = {}

        def fake_create_notifications_icon(**kwargs):
            captured.update(kwargs)
            return None

        monkeypatch.setattr(
            notifications_applet_mod,
            "create_notifications_icon",
            fake_create_notifications_icon,
        )
        applet.create_icon(size=48)
        assert captured["badge_count"] == 3

    def test_notification_activity_refreshes_presentation(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        monkeypatch.setattr(notifications_applet_mod.time, "monotonic", lambda: 100.0)
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _cb: 1,
        )
        applet.refresh_presentation = MagicMock()

        assert applet._on_notification_activity() is False
        assert applet._activity_until_monotonic == 108.0
        applet.refresh_presentation.assert_called_once()

    def test_notification_event_updates_last_content(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        monkeypatch.setattr(notifications_applet_mod.time, "monotonic", lambda: 5.0)
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _cb: 1,
        )
        applet.refresh_presentation = MagicMock()

        assert applet._on_notification_event("Mail", "New message", "Hello world") is False
        assert len(applet._history) == 1
        assert applet._history[0] == NotificationEntry(
            app_name="Mail",
            summary="New message",
            body="Hello world",
        )
        assert applet._history_index == 0
        applet.refresh_presentation.assert_called_once()

    def test_refresh_tooltip_includes_last_notification(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        applet._history = [
            NotificationEntry(app_name="Mail", summary="New message", body="Body content")
        ]
        applet._history_index = 0

        applet.refresh_tooltip()

        assert "Notification 1/1:" in applet.item.name
        assert "New message" in applet.item.name
        assert "App: Mail" in applet.item.name

    def test_extract_monitor_string(self):
        assert (
            NotificationsApplet._extract_monitor_string('   string "Docking test"')
            == "Docking test"
        )
        assert NotificationsApplet._extract_monitor_string("   int32 2000") is None

    def test_scroll_iterates_notification_history(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        applet._history = [
            NotificationEntry(app_name="A", summary="First", body=""),
            NotificationEntry(app_name="B", summary="Second", body=""),
            NotificationEntry(app_name="C", summary="Third", body=""),
        ]
        applet._history_index = 0
        applet.refresh_presentation = MagicMock()

        applet.on_scroll(direction_up=True)
        assert applet._history_index == 1

        applet.on_scroll(direction_up=False)
        assert applet._history_index == 0
        assert applet.refresh_presentation.call_count == 2

    def test_scroll_wraps_history(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        applet._history = [
            NotificationEntry(app_name="A", summary="First", body=""),
            NotificationEntry(app_name="B", summary="Second", body=""),
        ]
        applet._history_index = 0
        applet.on_scroll(direction_up=False)
        assert applet._history_index == 1

    def test_history_is_capped(self, monkeypatch):
        applet, _backend = _make_applet(monkeypatch, _state(pending_known=False))
        monkeypatch.setattr(notifications_applet_mod.time, "monotonic", lambda: 1.0)
        monkeypatch.setattr(
            notifications_applet_mod.GLib,
            "timeout_add_seconds",
            lambda _seconds, _cb: 1,
        )
        monkeypatch.setattr(notifications_applet_mod.GLib, "source_remove", lambda _id: None)
        applet.refresh_presentation = MagicMock()
        for i in range(HISTORY_LIMIT + 5):
            applet._on_notification_event("App", f"S{i}", "Body")
        assert len(applet._history) == HISTORY_LIMIT


class TestNotificationsRender:
    def test_icon_renders_sizes(self):
        for size in (32, 48, 64):
            pixbuf = create_notifications_icon(size=size, paused=False, badge_count=4)
            assert pixbuf is not None
            assert pixbuf.get_width() == size
            assert pixbuf.get_height() == size

    def test_icon_renders_activity_dot(self):
        pixbuf = create_notifications_icon(
            size=48,
            paused=False,
            badge_count=0,
            activity=True,
        )
        assert pixbuf is not None
        assert pixbuf.get_width() == 48
