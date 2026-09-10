"""Shared StatusNotifier/AppIndicator platform infrastructure."""

from docking.platform.status_notifier.backend import (
    StatusNotifierBackend,
    StatusTrayState,
    TrayItem,
)
from docking.platform.status_notifier.notifications import (
    StatusNotifierNotificationBridge,
)

__all__ = [
    "StatusNotifierBackend",
    "StatusNotifierNotificationBridge",
    "StatusTrayState",
    "TrayItem",
]
