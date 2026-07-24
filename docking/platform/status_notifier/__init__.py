"""Shared StatusNotifier/AppIndicator platform infrastructure."""

from docking.platform.status_notifier.backend import (
    DEFAULT_ITEM_PATH,
    RegisteredItemAddress,
    StatusNotifierBackend,
    StatusTrayState,
    TrayIconPixmap,
    TrayItem,
    parse_registered_item,
    tray_item_from_properties,
    unavailable_state,
)
from docking.platform.status_notifier.notifications import (
    POLL_INTERVAL_S,
    SLACK_DESKTOP_ID,
    SLACK_ITEM_PREFIX,
    StatusNotifierNotificationBridge,
    parse_slack_notification_count,
    status_notifier_desktop_id,
)

__all__ = [
    "DEFAULT_ITEM_PATH",
    "POLL_INTERVAL_S",
    "SLACK_DESKTOP_ID",
    "SLACK_ITEM_PREFIX",
    "RegisteredItemAddress",
    "StatusNotifierBackend",
    "StatusNotifierNotificationBridge",
    "StatusTrayState",
    "TrayIconPixmap",
    "TrayItem",
    "parse_registered_item",
    "parse_slack_notification_count",
    "status_notifier_desktop_id",
    "tray_item_from_properties",
    "unavailable_state",
]
