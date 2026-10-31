"""Typed native operations used by this applet."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import NotificationsState


class NotificationsBackend(ABC):
    """Required operations and metadata for notification backend adapters."""

    @property
    @abstractmethod
    def name(self) -> str: ...
    @property
    @abstractmethod
    def supports_clear(self) -> bool: ...

    @abstractmethod
    def get_state(self) -> NotificationsState: ...

    @abstractmethod
    def set_paused(self, paused: bool) -> bool: ...

    @abstractmethod
    def clear_notifications(self) -> bool: ...


class NotificationActivityMonitor(ABC):
    @abstractmethod
    def start(self) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...


class NotificationsOperations(ABC):
    @abstractmethod
    def detect_backend(self) -> NotificationsBackend: ...

    @abstractmethod
    def activity_monitor(
        self,
        *,
        on_event: Callable[[str, str, str], bool | None],
        on_activity: Callable[[], bool | None],
    ) -> NotificationActivityMonitor: ...
