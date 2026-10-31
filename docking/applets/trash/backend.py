"""Trash operations and result values without native imports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrashEmptyResult:
    """Outcome of an Empty Trash request as far as Docking can observe it."""

    attempted: bool
    before_count: int
    remaining_count: int
    failures: int = 0
    permission_denied: bool = False
    delegated: bool = False


class TrashBackend(ABC):
    """Desktop-specific trash behavior used by the Trash applet."""

    @property
    @abstractmethod
    def name(self) -> str: ...
    @property
    @abstractmethod
    def uri(self) -> str: ...

    @abstractmethod
    def count_items(self) -> int: ...

    @abstractmethod
    def watch_changes(self, changed: Callable[[], None]) -> Callable[[], None]: ...

    @abstractmethod
    def trash_uris(self, uris: list[str]) -> bool: ...

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def empty(self, confirm: Callable[[], bool]) -> TrashEmptyResult: ...
