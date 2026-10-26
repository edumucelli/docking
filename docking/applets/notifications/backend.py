"""Common notification control and activity interface, owned by one IO worker."""

from abc import ABC, abstractmethod

from docking.platform import environment

from .state import NotificationsSnapshot, NotificationsState


class NotificationsBackend(ABC):
    @abstractmethod
    def poll(self) -> NotificationsSnapshot: ...

    @abstractmethod
    def set_paused(self, paused: bool) -> bool: ...

    @abstractmethod
    def clear_notifications(self, state: NotificationsState) -> bool: ...

    @abstractmethod
    def stop(self) -> None:
        """Signal cancellation without blocking GTK."""

    def close(self) -> None:
        """Release resources on the worker after its final request."""
        return


def create_backend() -> NotificationsBackend:
    if environment.is_windows():
        from .windows import WindowsNotificationsBackend

        return WindowsNotificationsBackend()
    from .linux import LinuxNotificationsBackend

    return LinuxNotificationsBackend()
