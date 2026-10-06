"""Shared StatusNotifier/AppIndicator platform infrastructure."""

from docking.platform.status_notifier.backend import (
    StatusNotifierBackend,
    StatusTrayState,
    TrayItem,
)
from docking.platform.status_notifier.notifications import (
    StatusNotifierNotificationBridge,
)
from docking.platform.status_notifier.service import (
    POLL_INTERVAL_S,
    StatusNotifierService,
)

__all__ = [
    "POLL_INTERVAL_S",
    "StatusNotifierBackend",
    "StatusNotifierNotificationBridge",
    "StatusNotifierService",
    "StatusTrayState",
    "TrayItem",
]
