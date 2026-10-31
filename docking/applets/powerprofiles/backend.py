"""Typed native operations used by this applet."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import PowerProfilesState


class PowerProfilesControlBackend(ABC):
    """Backend contract used by the applet."""

    @abstractmethod
    def get_state(self) -> PowerProfilesState: ...

    @abstractmethod
    def set_active_profile(self, profile: str) -> bool: ...


class PowerprofilesOperations(ABC):
    @abstractmethod
    def detect_backend(self) -> PowerProfilesControlBackend: ...
