"""Shortcut lifecycle/status contract independent of native transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias


class GlobalShortcutsState(str, Enum):
    """Observable lifecycle and outcome states."""

    STOPPED = "stopped"
    STARTING = "starting"
    CREATING = "creating"
    BINDING = "binding"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    DENIED = "denied"
    BOUND = "bound"
    REASSIGNED = "reassigned"
    ERROR = "error"


@dataclass(frozen=True)
class GlobalShortcutBinding:
    """The portal's current presentation of the Docking shortcut."""

    shortcut_id: str
    description: str
    trigger_description: str | None = None


@dataclass(frozen=True)
class GlobalShortcutActivation:
    """An activation forwarded by the portal."""

    shortcut_id: str
    timestamp: int
    activation_token: str | None


@dataclass(frozen=True)
class GlobalShortcutsStatus:
    """A snapshot of service state suitable for UI or diagnostics."""

    state: GlobalShortcutsState
    portal_version: int | None = None
    session_handle: str | None = None
    binding: GlobalShortcutBinding | None = None
    message: str | None = None


ActivationCallback: TypeAlias = Callable[[GlobalShortcutActivation], None]
StatusCallback: TypeAlias = Callable[[GlobalShortcutsStatus], None]


class GlobalShortcutService(ABC):
    @property
    @abstractmethod
    def status(self) -> GlobalShortcutsStatus: ...
    @abstractmethod
    def start(self) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...
    @abstractmethod
    def list_shortcuts(self) -> bool: ...
