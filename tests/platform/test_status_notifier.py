"""Tests for runtime StatusNotifier-to-launcher notification overlays."""

from __future__ import annotations

from unittest.mock import MagicMock

import docking.platform.status_notifier as status_mod
from docking.applets.systemtray.state import (
    RegisteredItemAddress,
    StatusTrayState,
    tray_item_from_properties,
    unavailable_state,
)
from docking.platform.status_notifier import (
    POLL_INTERVAL_S,
    SLACK_DESKTOP_ID,
    StatusNotifierNotificationBridge,
    parse_slack_notification_count,
    status_notifier_desktop_id,
)


def _tray_item(
    *,
    item_id: str = "Slack_status_icon_1",
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
        worker = _ImmediateWorker()
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
        bridge = StatusNotifierNotificationBridge(
            model=model,
            backend=backend,
            worker=worker,
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

    def test_disappearing_item_removes_overlay(self):
        model = MagicMock()
        bridge = StatusNotifierNotificationBridge(
            model=model,
            backend=_Backend(_available_state()),
            worker=_ImmediateWorker(),
        )
        bridge._running = True
        bridge._on_state_result(_available_state(_tray_item()))
        model.reset_mock()

        bridge._on_state_result(_available_state())

        model.remove_status_notifier_overlay.assert_called_once_with(
            source_id=":1.42/StatusNotifierItem"
        )

    def test_unavailable_backend_preserves_existing_overlay(self):
        model = MagicMock()
        bridge = StatusNotifierNotificationBridge(
            model=model,
            backend=_Backend(_available_state()),
            worker=_ImmediateWorker(),
        )
        bridge._running = True
        bridge._on_state_result(_available_state(_tray_item()))
        model.reset_mock()

        bridge._on_state_result(unavailable_state("session bus unavailable"))

        model.remove_status_notifier_overlay.assert_not_called()
        model.apply_status_notifier_overlay.assert_not_called()

    def test_malformed_tooltip_does_not_clear_previous_count(self):
        model = MagicMock()
        bridge = StatusNotifierNotificationBridge(
            model=model,
            backend=_Backend(_available_state()),
            worker=_ImmediateWorker(),
        )
        bridge._running = True
        bridge._on_state_result(_available_state(_tray_item()))
        model.reset_mock()

        bridge._on_state_result(
            _available_state(_tray_item(tooltip_title="Connecting to Slack"))
        )

        model.apply_status_notifier_overlay.assert_not_called()
        model.remove_status_notifier_overlay.assert_not_called()

    def test_unknown_tray_items_are_ignored(self):
        model = MagicMock()
        bridge = StatusNotifierNotificationBridge(
            model=model,
            backend=_Backend(_available_state()),
            worker=_ImmediateWorker(),
        )
        bridge._running = True

        bridge._on_state_result(
            _available_state(_tray_item(item_id="Example_status_icon"))
        )

        model.apply_status_notifier_overlay.assert_not_called()

    def test_poll_failure_preserves_overlays_and_keeps_timer_alive(self):
        model = MagicMock()
        backend = _RaisingBackend(_available_state())
        bridge = StatusNotifierNotificationBridge(
            model=model,
            backend=backend,
            worker=_ImmediateWorker(),
        )
        bridge._running = True
        bridge._observed_source_ids.add(":1.42/StatusNotifierItem")

        assert bridge._tick() is True

        model.remove_status_notifier_overlay.assert_not_called()
        assert bridge._observed_source_ids == {":1.42/StatusNotifierItem"}

    def test_late_result_after_stop_is_ignored(self):
        model = MagicMock()
        bridge = StatusNotifierNotificationBridge(
            model=model,
            backend=_Backend(_available_state()),
            worker=_ImmediateWorker(),
        )

        bridge._on_state_result(_available_state(_tray_item()))

        model.apply_status_notifier_overlay.assert_not_called()
