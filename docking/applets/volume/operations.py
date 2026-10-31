"""Typed native operations used by this applet."""

from __future__ import annotations

from abc import ABC, abstractmethod


class VolumeOperations(ABC):
    @abstractmethod
    def volume_settings_command(self) -> list[str] | None: ...

    @abstractmethod
    def open_volume_settings(self) -> bool: ...
