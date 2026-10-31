"""Typed native operations used by this applet."""

from __future__ import annotations

from abc import ABC, abstractmethod

from .state import (
    Mode,
    Tool,
)


class ScreenshotOperations(ABC):
    @abstractmethod
    def _detect_tool(self) -> Tool | None: ...

    @abstractmethod
    def _run(self, tool: Tool, mode: Mode, delay_seconds: int = 0) -> list[str]: ...
