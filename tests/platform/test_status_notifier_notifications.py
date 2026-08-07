"""Tests for StatusNotifier-to-launcher notification overlays."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import docking.platform.status_notifier.notifications as status_mod
from docking.platform.status_notifier import (
    POLL_INTERVAL_S,
    SLACK_DESKTOP_ID,
    RegisteredItemAddress,
    StatusNotifierNotificationBridge,
    StatusTrayState,
    parse_slack_notification_count,
    status_notifier_desktop_id,
    tray_item_from_properties,
    unavailable_state,
)


def _tray_item(
    *,
    item_id: str = "Slack_status_icon_1",
    title: str = "",
    tooltip_title: str = "You have 2 notifications",
    tooltip_text: str = "",
):
    return tray_item_from_properties(
        address=RegisteredItemAddress(
            service=":1.42",
            path="/StatusNotifierItem",
        ),
        properties={
            "Id": item_id,
            "Title": title,
            "Status": "Active",
            "ToolTip": ("", [], tooltip_title, tooltip_text),
        },
    )


def _available_state(*items) -> StatusTrayState:
    return StatusTrayState(
        available=True,
        watcher_mode="watcher",
        items=tuple(items),
    )


class _Backend:
    def __init__(self, state: StatusTrayState) -> None:
        self.state = state
        self.close_calls = 0
        self.get_state_calls = 0

    def get_state(self) -> StatusTrayState:
        self.get_state_calls += 1
        return self.state

    def close(self) -> None:
        self.close_calls += 1


class _RaisingBackend(_Backend):
    def get_state(self) -> StatusTrayState:
        self.get_state_calls += 1
        raise RuntimeError("D-Bus unavailable")


class _ImmediateWorker:
    def run_guarded(
        self,
        *,
        fn,
        on_result=None,
        on_error=None,
        **_kwargs,
    ) -> bool:
        try:
            result = fn()
        except Exception as exc:
            if on_error is not None:
                on_error(exc)
            return True
        if on_result is not None:
            on_result(result)
        return True


class _DeferredWorker:
    def __init__(self) -> None:
        self._fn = None
        self._on_result = None
        self._result = None

    def run_guarded(self, *, fn, on_result=None, **_kwargs) -> bool:
        self._fn = fn
        self._on_result = on_result
        return True

    def run_background(self) -> None:
        assert self._fn is not None
        self._result = self._fn()

    def deliver_result(self) -> None:
        assert self._on_result is not None
        self._on_result(self._result)


def _registry(*, exact=None, alias=None):
    registry = MagicMock()
    registry.resolve.return_value = exact
    registry.resolve_by_wm_class.return_value = alias
    return registry


def _bridge(
    monkeypatch,
    *,
    model,
    backend,
    application_registry=None,
    worker=None,
) -> StatusNotifierNotificationBridge:
    registry = application_registry if application_registry is not None else _registry()
    bridge_worker = worker if worker is not None else _ImmediateWorker()
    monkeypatch.setattr(status_mod, "StatusNotifierBackend", lambda: backend)
    monkeypatch.setattr(
        status_mod,
        "BackgroundWorker",
        lambda **_kwargs: bridge_worker,
    )
    return StatusNotifierNotificationBridge(
        model=model,
        application_registry=registry,
    )


class TestSlackTrayParsing:
    def test_identifies_slack_status_item(self):
        assert status_notifier_desktop_id(_tray_item()) == SLACK_DESKTOP_ID
        titled = tray_item_from_properties(
            address=RegisteredItemAddress(service=":1.43", path="/StatusNotifierItem"),
            properties={
                "Id": "Slack_status_icon_2",
                "Title": "Workspace",
                "ToolTip": ("", [], "You have 1 notification", ""),
            },
        )
        assert status_notifier_desktop_id(titled) == SLACK_DESKTOP_ID
        assert (
            status_notifier_desktop_id(_tray_item(item_id="Example_status_icon"))
            is None
        )

    def test_extracts_count_from_title_or_body(self):
        assert parse_slack_notification_count(_tray_item()) == 2
        assert (
            parse_slack_notification_count(
                _tray_item(
                    tooltip_title="Slack",
                    tooltip_text="Você tem 7 notificações",
                )
            )
            == 7
        )

    def test_understands_english_zero_and_rejects_malformed_tooltips(self):
        assert (
            parse_slack_notification_count(
                _tray_item(tooltip_title="You have no notifications")
            )
            == 0
        )
        assert (
            parse_slack_notification_count(
                _tray_item(tooltip_title="You have no unread messages")
            )
            == 0
        )
        assert (
            parse_slack_notification_count(
                _tray_item(tooltip_title="You have unread messages")
            )
            == 1
        )
        assert (
            parse_slack_notification_count(
                _tray_item(tooltip_title="Connecting to Slack")
            )
            is None
        )
        assert (
            parse_slack_notification_count(
                _tray_item(tooltip_title="", tooltip_text="")
            )
            is None
        )


class TestStatusNotifierNotificationBridge:
    def test_start_polls_and_stop_clears_observed_overlay(
        self,
        monkeypatch,
    ):
        model = MagicMock()
        backend = _Backend(_available_state(_tray_item()))
        timer_calls: list[tuple[int, object]] = []
        removed_timers: list[int] = []
        monkeypatch.setattr(
            status_mod.GLib,
            "timeout_add_seconds",
            lambda seconds, callback: timer_calls.append((seconds, callback)) or 77,
        )
        monkeypatch.setattr(
            status_mod.GLib,
            "source_remove",
            lambda timer_id: removed_timers.append(timer_id),
        )
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=backend,
        )

        bridge.start()
        bridge.start()

        model.apply_status_notifier_overlay.assert_called_once_with(
            source_id=":1.42/StatusNotifierItem",
            desktop_id="slack.desktop",
            badge_count=2,
        )
        assert backend.get_state_calls == 1
        assert timer_calls == [(POLL_INTERVAL_S, bridge._tick)]

        bridge.stop()

        model.remove_status_notifier_overlay.assert_called_once_with(
            source_id=":1.42/StatusNotifierItem"
        )
        assert removed_timers == [77]
        assert backend.close_calls == 1

    def test_exact_registry_match_uses_canonical_desktop_id(self, monkeypatch):
        model = MagicMock()
        installed = SimpleNamespace(desktop_id="com.slack.Slack.desktop")
        registry = _registry(exact=installed)
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=_Backend(_available_state()),
            application_registry=registry,
        )
        bridge._running = True

        bridge._on_state_result(_available_state(_tray_item()))

        registry.resolve.assert_called_once_with(
            SLACK_DESKTOP_ID,
            log_failures=False,
        )
        registry.resolve_by_wm_class.assert_not_called()
        model.apply_status_notifier_overlay.assert_called_once_with(
            source_id=":1.42/StatusNotifierItem",
            desktop_id="com.slack.Slack.desktop",
            badge_count=2,
        )

    def test_alias_registry_match_uses_canonical_desktop_id(self, monkeypatch):
        model = MagicMock()
        installed = SimpleNamespace(desktop_id="com.slack.Slack.desktop")
        registry = _registry(alias=installed)
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=_Backend(_available_state()),
            application_registry=registry,
        )
        bridge._running = True

        bridge._on_state_result(_available_state(_tray_item()))

        registry.resolve_by_wm_class.assert_called_once_with("slack")
        model.apply_status_notifier_overlay.assert_called_once_with(
            source_id=":1.42/StatusNotifierItem",
            desktop_id="com.slack.Slack.desktop",
            badge_count=2,
        )

    def test_unresolved_registry_lookup_keeps_exact_slack_fallback(
        self,
        monkeypatch,
    ):
        model = MagicMock()
        registry = _registry()
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=_Backend(_available_state()),
            application_registry=registry,
        )
        bridge._running = True

        bridge._on_state_result(_available_state(_tray_item()))

        registry.resolve_by_wm_class.assert_called_once_with("slack")
        model.apply_status_notifier_overlay.assert_called_once_with(
            source_id=":1.42/StatusNotifierItem",
            desktop_id=SLACK_DESKTOP_ID,
            badge_count=2,
        )

    def test_registry_lookup_waits_for_main_thread_result_callback(
        self,
        monkeypatch,
    ):
        model = MagicMock()
        backend = _Backend(_available_state(_tray_item()))
        registry = _registry()
        worker = _DeferredWorker()
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=backend,
            application_registry=registry,
            worker=worker,
        )
        bridge._running = True

        bridge._poll_async()
        worker.run_background()

        assert backend.get_state_calls == 1
        registry.resolve.assert_not_called()
        registry.resolve_by_wm_class.assert_not_called()

        worker.deliver_result()

        registry.resolve.assert_called_once_with(
            SLACK_DESKTOP_ID,
            log_failures=False,
        )
        model.apply_status_notifier_overlay.assert_called_once()

    def test_disappearing_item_removes_overlay(self, monkeypatch):
        model = MagicMock()
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=_Backend(_available_state()),
        )
        bridge._running = True
        bridge._on_state_result(_available_state(_tray_item()))
        model.reset_mock()

        bridge._on_state_result(_available_state())

        model.remove_status_notifier_overlay.assert_called_once_with(
            source_id=":1.42/StatusNotifierItem"
        )

    def test_unavailable_backend_preserves_existing_overlay(self, monkeypatch):
        model = MagicMock()
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=_Backend(_available_state()),
        )
        bridge._running = True
        bridge._on_state_result(_available_state(_tray_item()))
        model.reset_mock()

        bridge._on_state_result(unavailable_state("session bus unavailable"))

        model.remove_status_notifier_overlay.assert_not_called()
        model.apply_status_notifier_overlay.assert_not_called()

    def test_malformed_tooltip_does_not_clear_previous_count(self, monkeypatch):
        model = MagicMock()
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=_Backend(_available_state()),
        )
        bridge._running = True
        bridge._on_state_result(_available_state(_tray_item()))
        model.reset_mock()

        bridge._on_state_result(
            _available_state(_tray_item(tooltip_title="Connecting to Slack"))
        )

        model.apply_status_notifier_overlay.assert_not_called()
        model.remove_status_notifier_overlay.assert_not_called()

    def test_unknown_tray_items_are_ignored(self, monkeypatch):
        model = MagicMock()
        registry = _registry()
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=_Backend(_available_state()),
            application_registry=registry,
        )
        bridge._running = True

        bridge._on_state_result(
            _available_state(
                _tray_item(item_id="Example_status_icon"),
                _tray_item(item_id="", title="Slack"),
            )
        )

        model.apply_status_notifier_overlay.assert_not_called()
        registry.resolve.assert_not_called()
        registry.resolve_by_wm_class.assert_not_called()

    def test_poll_failure_preserves_overlays_and_keeps_timer_alive(self, monkeypatch):
        model = MagicMock()
        backend = _RaisingBackend(_available_state())
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=backend,
        )
        bridge._running = True
        bridge._observed_source_ids.add(":1.42/StatusNotifierItem")

        assert bridge._tick() is True

        model.remove_status_notifier_overlay.assert_not_called()
        assert bridge._observed_source_ids == {":1.42/StatusNotifierItem"}

    def test_late_result_after_stop_is_ignored(self, monkeypatch):
        model = MagicMock()
        bridge = _bridge(
            monkeypatch,
            model=model,
            backend=_Backend(_available_state()),
        )

        bridge._on_state_result(_available_state(_tray_item()))

        model.apply_status_notifier_overlay.assert_not_called()
