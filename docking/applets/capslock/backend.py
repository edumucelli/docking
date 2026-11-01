"""Keyboard lock indicators shared by the platform implementations."""

from abc import ABC, abstractmethod

from docking.applets.capslock.state import LockKeyState


class LockKeyBackend(ABC):
    @abstractmethod
    def get_state(self) -> LockKeyState:
        """Read Caps Lock and Num Lock toggle indicators without changing them."""
        ...
