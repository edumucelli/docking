"""Brightness and optional backlight capabilities without tool-specific state."""

from abc import ABC, abstractmethod
from pathlib import Path


class BacklightSource(ABC):
    @abstractmethod
    def find(self) -> Path | None: ...
    @abstractmethod
    def read(self, path: Path) -> float | None: ...
    @abstractmethod
    def write(self, path: Path, value: float) -> bool: ...


class BrightnessOperations(ABC):
    @abstractmethod
    def available(self) -> bool: ...
    @abstractmethod
    def get_brightness(self) -> float | None: ...
    @abstractmethod
    def set_brightness(self, value: float) -> bool: ...
