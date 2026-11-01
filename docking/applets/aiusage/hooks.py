"""Headless provider-hook registration policy supplied by host composition."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from docking.platform.host.base import HostServices


class UsageHooks(ABC):
    def __init__(self, host: HostServices, *, root: Path, executable: str) -> None:
        self._host, self._root, self._executable = host, root, executable

    @abstractmethod
    def update_claude(self, hooks: dict[str, Any]) -> bool: ...

    @abstractmethod
    def codex_notify_is_current(self, existing: str, desired: str) -> bool: ...
