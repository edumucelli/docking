"""Thermal reader contract without presentation or native runtime imports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .state import ThermalSnapshot


class ThermalsBackend(ABC):
    """Backend interface for reading current thermal state."""

    @abstractmethod
    def get_snapshot(self) -> ThermalSnapshot:
        """Read current thermal state."""
        raise NotImplementedError

    @staticmethod
    def has_readings(snapshot: ThermalSnapshot) -> bool:
        """Return whether a snapshot contains live thermal data."""
        return snapshot.hottest is not None or snapshot.fan is not None
