"""Common persistent browser session and validated badge/status policy."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from docking.platform import targets

from .bridge import BridgeEvent
from .profile import BrowserProfile as BrowserProfile
from .state import BrowserPhase

PhaseCallback = Callable[[BrowserPhase, str], None]
TitleCallback = Callable[[str], None]
BadgeCallback = Callable[[int | None, bool], None]
NotificationCallback = Callable[[], None]
ClearCallback = Callable[[bool, str], None]


class WebKitUnavailableError(RuntimeError):
    """No supported WebKitGTK runtime is installed."""


class BrowserBackend(ABC):
    def __init__(
        self,
        *,
        profile: BrowserProfile,
        on_phase: PhaseCallback,
        on_title: TitleCallback,
        on_badge: BadgeCallback,
        on_notification: NotificationCallback,
        on_attention_cleared: NotificationCallback,
    ) -> None:
        self.profile = profile
        self._on_phase, self._on_title, self._on_badge = on_phase, on_title, on_badge
        self._on_notification = on_notification
        self._on_attention_cleared = on_attention_cleared
        self._phase = BrowserPhase.STOPPED
        self._last_bridge_badge: tuple[int | None, bool] | None = None
        self._last_bridge_status: tuple[bool, str] | None = None

    @property
    def phase(self) -> BrowserPhase:
        return self._phase

    @property
    @abstractmethod
    def visible(self) -> bool: ...

    @abstractmethod
    def start(self) -> bool: ...

    @abstractmethod
    def show(self) -> None: ...

    @abstractmethod
    def hide(self) -> None: ...

    def toggle(self) -> None:
        self.hide() if self.visible else self.show()

    @abstractmethod
    def reload(self) -> None: ...

    @abstractmethod
    def clear_session(self, on_complete: ClearCallback) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @staticmethod
    def _open_external_uri(uri: str) -> None:
        targets.open_target(uri)

    def _set_phase(self, phase: BrowserPhase, error: str = "") -> None:
        if phase == self._phase and not error:
            return
        self._phase = phase
        self._on_phase(phase, error)

    def _apply_bridge_event(self, event: BridgeEvent) -> None:
        if event.kind == "badge":
            current = (event.badge_count, event.badge_visible)
            if current != self._last_bridge_badge:
                self._last_bridge_badge = current
                self._on_badge(event.badge_count, event.badge_visible)
        elif event.kind == "status":
            self._apply_status_event(event)
        else:
            self._activate_notification()

    def _activate_notification(self) -> None:
        self.show()
        self._on_attention_cleared()

    def _apply_status_event(self, event: BridgeEvent) -> None:
        if event.online is None:
            return
        current = (event.online, event.auth)
        if current == self._last_bridge_status:
            return
        self._last_bridge_status = current
        if not event.online:
            self._set_phase(BrowserPhase.OFFLINE)
        elif event.auth == "login_required":
            self._set_phase(BrowserPhase.LOGIN_REQUIRED)
        elif event.auth == "ready":
            self._set_phase(BrowserPhase.READY)
        else:
            self._set_phase(BrowserPhase.SYNCING)
