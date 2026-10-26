"""Keyboard lock indicators shared by the platform implementations."""

from abc import ABC, abstractmethod

from docking.applets.capslock.state import LockKeyState
from docking.platform import environment


class LockKeyBackend(ABC):
    @abstractmethod
    def get_state(self) -> LockKeyState:
        """Read Caps Lock and Num Lock toggle indicators without changing them."""
        ...


def create_backend() -> LockKeyBackend:
    if environment.is_windows():
        from docking.applets.capslock.windows import WindowsLockKeyBackend

        return WindowsLockKeyBackend()
    from docking.applets.capslock.linux import X11LockKeyBackend

    return X11LockKeyBackend()
