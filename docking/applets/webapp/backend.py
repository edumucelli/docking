"""Persistent browser operations and typed construction callbacks."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Protocol

from .profile import BrowserProfile
from .state import BrowserPhase

PhaseCallback = Callable[[BrowserPhase, str], None]
TitleCallback = Callable[[str], None]
BadgeCallback = Callable[[int | None, bool], None]
NotificationCallback = Callable[[], None]
ClearCallback = Callable[[bool, str], None]


class BrowserBackend(ABC):
    @property
    @abstractmethod
    def phase(self) -> BrowserPhase: ...

    @property
    @abstractmethod
    def visible(self) -> bool: ...

    @abstractmethod
    def start(self) -> bool: ...

    @abstractmethod
    def show(self) -> None: ...

    @abstractmethod
    def hide(self) -> None: ...

    @abstractmethod
    def toggle(self) -> None: ...

    @abstractmethod
    def reload(self) -> None: ...

    @abstractmethod
    def clear_session(self, on_complete: ClearCallback) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...


class BrowserFactory(Protocol):
    def __call__(
        self,
        *,
        profile: BrowserProfile,
        on_phase: PhaseCallback,
        on_title: TitleCallback,
        on_badge: BadgeCallback,
        on_notification: NotificationCallback,
        on_attention_cleared: NotificationCallback,
    ) -> BrowserBackend: ...
