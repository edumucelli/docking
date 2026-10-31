"""Compatibility names for the platform-independent tray notification bridge."""

from docking.platform.tray.notifications import (
    SLACK_DESKTOP_ID,
    SLACK_ITEM_PREFIX,
    TrayNotificationBridge,
    parse_slack_notification_count,
    status_notifier_desktop_id,
)

StatusNotifierNotificationBridge = TrayNotificationBridge

__all__ = [
    "SLACK_DESKTOP_ID",
    "SLACK_ITEM_PREFIX",
    "StatusNotifierNotificationBridge",
    "parse_slack_notification_count",
    "status_notifier_desktop_id",
]
