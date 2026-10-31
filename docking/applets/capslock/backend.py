"""Typed native operations used by this applet."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .state import LockKeyState


class CapslockOperations(ABC):
    @abstractmethod
    def query_lock_state(self) -> LockKeyState: ...
