"""Runtime bridge from StatusNotifier items to pinned launcher overlays."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from gi.repository import GLib

from docking.applets.worker import BackgroundWorker
from docking.log import get_logger, with_context
from docking.platform.status_notifier.backend import (
    StatusNotifierBackend,
    StatusTrayState,
    TrayItem,
)

if TYPE_CHECKING:
    from docking.platform.applications.registry import ApplicationRegistry
    from docking.platform.model import DockModel

POLL_INTERVAL_S = 3
SLACK_DESKTOP_ID = "slack.desktop"
SLACK_ITEM_PREFIX = "slack_status_icon"

_COUNT_RE = re.compile(r"(?<!\d)(\d{1,6})(?!\d)")
_ZERO_NOTIFICATION_RE = re.compile(
    r"\b(?:no|zero)\s+(?:(?:new|unread)\s+)?(?:notifications?|messages?)\b",
    re.IGNORECASE,
)
_UNREAD_NOTIFICATION_RE = re.compile(
    r"\b(?:new|unread)\s+(?:notifications?|messages?)\b",
    re.IGNORECASE,
)

log = with_context(get_logger(name="status_notifier"), component="notification_bridge")


def status_notifier_desktop_id(item: TrayItem) -> str | None:
    """Map supported tray item identities to desktop IDs."""
    item_id = item.item_id or item.title
    if item_id.casefold().startswith(SLACK_ITEM_PREFIX):
        return SLACK_DESKTOP_ID
    return None


def parse_slack_notification_count(item: TrayItem) -> int | None:
    """Extract Slack's unread count from its localized tooltip when possible."""
    tooltip = " ".join(
        part.strip() for part in (item.tooltip_title, item.tooltip_text) if part.strip()
    )
    if not tooltip:
        return None
    match = _COUNT_RE.search(tooltip)
    if match is not None:
        return int(match.group(1))
    if _ZERO_NOTIFICATION_RE.search(tooltip):
        return 0
    if _UNREAD_NOTIFICATION_RE.search(tooltip):
        # Slack sometimes exposes only a boolean unread state. Keep the badge
        # and urgency useful even when the exact count is unavailable.
        return 1
    return None


class StatusNotifierNotificationBridge:
    """Poll tray metadata and publish supported launcher notification overlays."""

    def __init__(
        self,
        *,
        model: DockModel,
        application_registry: ApplicationRegistry,
    ) -> None:
        self._model = model
        self._application_registry = application_registry
        self._backend = StatusNotifierBackend()
        self._worker = BackgroundWorker(logger=log)
        self._timer_id = 0
        self._running = False
        self._observed_source_ids: set[str] = set()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._poll_async()
        self._timer_id = GLib.timeout_add_seconds(
            POLL_INTERVAL_S,
            self._tick,
        )

    def stop(self) -> None:
        self._running = False
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        for source_id in tuple(self._observed_source_ids):
            self._model.remove_status_notifier_overlay(source_id=source_id)
        self._observed_source_ids.clear()
        self._backend.close()

    def _tick(self) -> bool:
        self._poll_async()
        return self._running

    def _poll_async(self) -> None:
        if not self._running:
            return
        self._worker.run_guarded(
            key="status-notifier-notifications",
            name="status-notifier-notifications-poll",
            fn=self._backend.get_state,
            on_result=self._on_state_result,
            on_error=self._on_poll_error,
        )

    def _canonical_desktop_id(self, desktop_id: str) -> str:
        application = self._application_registry.resolve(
            desktop_id,
            log_failures=False,
        )
        if application is None:
            application = self._application_registry.resolve_by_wm_class(
                desktop_id.removesuffix(".desktop")
            )
        return application.desktop_id if application is not None else desktop_id

    def _on_state_result(self, state: StatusTrayState) -> bool:
        if not self._running:
            return False
        if not state.available:
            log.bind(action="poll").debug(
                "StatusNotifier unavailable; preserving launcher overlays: %s",
                state.error,
            )
            return False

        current_source_ids: set[str] = set()
        for item in state.items:
            desktop_id = status_notifier_desktop_id(item)
            if desktop_id is None:
                continue
            desktop_id = self._canonical_desktop_id(desktop_id)
            current_source_ids.add(item.identifier)
            count = parse_slack_notification_count(item)
            if count is None:
                log.bind(action="parse", source=item.identifier).debug(
                    "Could not parse notification count from tooltip"
                )
                continue
            self._model.apply_status_notifier_overlay(
                source_id=item.identifier,
                desktop_id=desktop_id,
                badge_count=count,
            )

        for source_id in self._observed_source_ids - current_source_ids:
            self._model.remove_status_notifier_overlay(source_id=source_id)
        self._observed_source_ids = current_source_ids
        return False

    def _on_poll_error(self, exc: Exception) -> bool:
        if self._running:
            log.bind(action="poll").debug(
                "StatusNotifier notification poll failed; preserving overlays: %s",
                exc,
            )
        return False
