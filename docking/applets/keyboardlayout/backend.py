"""Typed native operations used by this applet."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .state import (
    LayoutBackend,
)


class KeyboardlayoutOperations(ABC):
    @abstractmethod
    def keyboard_settings_command(self) -> list[str] | None: ...

    @abstractmethod
    def current_layout_command(self, layout_code: str) -> list[str] | None: ...

    @abstractmethod
    def open_keyboard_settings(self) -> bool: ...

    @abstractmethod
    def show_current_layout(self, layout_code: str) -> bool: ...

    @abstractmethod
    def detect_backend(self) -> LayoutBackend: ...
