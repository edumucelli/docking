"""Camera privacy operations used by the applet."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .state import CamshieldState


class CamshieldBackend(ABC):
    @abstractmethod
    def probe(self) -> CamshieldState: ...
    @abstractmethod
    def helper_available(self) -> bool: ...
    @abstractmethod
    def perform(self, action: str) -> bool:
        """Return whether the native action ran and requires a fresh probe."""
        ...
