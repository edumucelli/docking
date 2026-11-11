"""Shared registration lifecycle and status presentation for dock shortcuts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from docking.i18n import _
from docking.search.app_identity import application_id
from docking.search.services.global_shortcuts import (
    TOGGLE_SEARCH_SHORTCUT_ID,
    GlobalShortcutActivation,
    GlobalShortcutService,
    GlobalShortcutsState,
    GlobalShortcutsStatus,
)
from docking.search.services.platform import SearchIntegration, ShortcutFallback

_XDG_MODIFIERS = ("CTRL", "ALT", "SHIFT", "LOGO", "NUM")


def shortcut_label(shortcut: str) -> str:
    """Return a compact user-facing label for an XDG shortcut string."""
    parts = [part.strip() for part in shortcut.split("+") if part.strip()]
    if not parts:
        return _("Not set")
    labels = {
        "CTRL": _("Ctrl"),
        "ALT": _("Alt"),
        "SHIFT": _("Shift"),
        "LOGO": _("Super"),
        "NUM": _("Num"),
        "space": _("Space"),
        "Return": _("Enter"),
        "Escape": _("Escape"),
        "Tab": _("Tab"),
        "BackSpace": _("Backspace"),
    }
    rendered = [
        labels.get(part.upper(), labels.get(part, part))
        if part.upper() in _XDG_MODIFIERS
        else labels.get(part, part)
        for part in parts
    ]
    return "+".join(rendered)


class ShortcutController(ABC):
    shortcut_id = TOGGLE_SEARCH_SHORTCUT_ID
    shortcut_description = _("Toggle Docking search")

    def _init_shortcuts(
        self,
        integration: SearchIntegration,
        schedule_idle: Callable[..., int],
        remove_source: Callable[[int], bool],
    ) -> None:
        self._integration = integration
        self._schedule_idle = schedule_idle
        self._remove_source = remove_source
        self._started = False
        self._shortcut_suspended = False
        self._shortcut_status: GlobalShortcutsStatus | None = None
        self._shortcut_status_listeners: list[Callable[[], None]] = []
        self._global_shortcuts = self._new_shortcut_service()
        self._shortcut_fallback = self._new_shortcut_fallback()
        self._shortcut_preferences = self._current_shortcut_preferences()

    @abstractmethod
    def _current_shortcut_preferences(self) -> tuple[bool, str]: ...

    @abstractmethod
    def _on_global_shortcut(self, activation: GlobalShortcutActivation) -> None: ...

    def _on_fallback_shortcut(self, timestamp: int) -> None:
        self._on_global_shortcut(
            GlobalShortcutActivation(self.shortcut_id, timestamp, None)
        )

    @property
    def shortcut_active(self) -> bool:
        if self._shortcut_fallback is not None and self._shortcut_fallback.active:
            return True
        return bool(
            self._shortcut_status
            and self._shortcut_status.state
            in {
                GlobalShortcutsState.BOUND,
                GlobalShortcutsState.REASSIGNED,
            }
            and self._shortcut_status.binding
            and self._shortcut_status.binding.trigger_description
        )

    @property
    def shortcut_status(self) -> GlobalShortcutsStatus | None:
        """Return the latest shortcut backend status, if available."""
        return self._shortcut_status

    def shortcut_status_text(self) -> str:
        """Return detailed shortcut status suitable for a diagnostic tooltip."""
        if self._shortcut_fallback is not None and self._shortcut_fallback.active:
            return _("Active: {shortcut} (X11)").format(
                shortcut=shortcut_label(self._current_shortcut_preferences()[1])
            )
        if self._shortcut_fallback is not None and self._shortcut_fallback.error:
            if self._shortcut_fallback.error == "shortcut is already in use":
                return _("Shortcut already in use")
            return _("Registration failed")
        status = self._shortcut_status
        if status is None:
            return _("Not active")
        if (
            status.state is GlobalShortcutsState.ERROR
            and status.message == "shortcut is already in use"
        ):
            return _("Shortcut already in use")
        trigger = status.binding.trigger_description if status.binding else None
        if status.state in {
            GlobalShortcutsState.STARTING,
            GlobalShortcutsState.CREATING,
            GlobalShortcutsState.BINDING,
        }:
            return _("Connecting...")
        if status.state in {
            GlobalShortcutsState.BOUND,
            GlobalShortcutsState.REASSIGNED,
        }:
            if trigger:
                return _("Assigned: {shortcut}").format(shortcut=trigger)
            return _("Assigned by desktop")
        if status.state is GlobalShortcutsState.UNAVAILABLE:
            return _("Unavailable on this desktop")
        if status.state is GlobalShortcutsState.DENIED:
            return _("Permission denied")
        if status.state is GlobalShortcutsState.CANCELLED:
            return _("Setup cancelled")
        if status.state is GlobalShortcutsState.ERROR:
            return _("Registration failed")
        return _("Not active")

    def shortcut_status_summary(self) -> str:
        """Return the short shortcut state displayed below Preferences."""
        if self._shortcut_fallback is not None:
            if self._shortcut_fallback.active:
                return _("Active")
            if self._shortcut_fallback.error:
                if self._shortcut_fallback.error == "shortcut is already in use":
                    return _("Conflict")
                return _("Failed")
        status = self._shortcut_status
        if status is None:
            return _("Inactive")
        if (
            status.state is GlobalShortcutsState.ERROR
            and status.message == "shortcut is already in use"
        ):
            return _("Conflict")
        if status.state in {
            GlobalShortcutsState.STARTING,
            GlobalShortcutsState.CREATING,
            GlobalShortcutsState.BINDING,
        }:
            return _("Connecting")
        if status.state in {
            GlobalShortcutsState.BOUND,
            GlobalShortcutsState.REASSIGNED,
        }:
            return _("Active")
        if status.state is GlobalShortcutsState.UNAVAILABLE:
            return _("Unavailable")
        if status.state is GlobalShortcutsState.DENIED:
            return _("Denied")
        if status.state is GlobalShortcutsState.CANCELLED:
            return _("Cancelled")
        if status.state is GlobalShortcutsState.ERROR:
            return _("Failed")
        return _("Inactive")

    def add_shortcut_status_listener(
        self,
        listener: Callable[[], None],
    ) -> Callable[[], None]:
        """Subscribe to status changes and return an idempotent unsubscriber."""
        if listener not in self._shortcut_status_listeners:
            self._shortcut_status_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._shortcut_status_listeners:
                self._shortcut_status_listeners.remove(listener)

        return unsubscribe

    def suspend_shortcuts(self) -> None:
        """Temporarily release global grabs while a new sequence is captured."""
        self._shortcut_suspended = True
        self._stop_shortcut_services()
        self._notify_shortcut_status()

    def resume_shortcuts(self) -> None:
        """Restore shortcut services after capture or another temporary pause."""
        self._shortcut_suspended = False
        if self._started and self._current_shortcut_preferences()[0]:
            self._start_shortcut_services()

    def _new_shortcut_service(self) -> GlobalShortcutService:
        return self._integration.primary(
            app_id=application_id(),
            preferred_trigger=self._current_shortcut_preferences()[1],
            trigger_description=shortcut_label(self._current_shortcut_preferences()[1]),
            on_activated=self._on_global_shortcut,
            on_status_changed=self._on_shortcut_status,
            schedule_idle=self._schedule_idle,
            remove_source=self._remove_source,
            shortcut_id=self.shortcut_id,
            description=self.shortcut_description,
        )

    def _new_shortcut_fallback(self) -> ShortcutFallback | None:
        return self._integration.fallback(
            shortcut=self._current_shortcut_preferences()[1],
            on_activated=self._on_fallback_shortcut,
            schedule_idle=self._schedule_idle,
        )

    def _start_shortcut_services(self) -> None:
        self._global_shortcuts.start()

    def _stop_shortcut_services(self) -> None:
        self._global_shortcuts.stop()
        if self._shortcut_fallback is not None:
            self._shortcut_fallback.stop()

    def _start_shortcut_fallback(self) -> None:
        if self._shortcut_fallback is not None:
            self._shortcut_fallback.start()

    def _on_shortcut_status(self, status: GlobalShortcutsStatus) -> None:
        self._shortcut_status = status
        if (
            not self._started
            or self._shortcut_suspended
            or not self._current_shortcut_preferences()[0]
        ):
            return
        if status.state is GlobalShortcutsState.UNAVAILABLE:
            self._start_shortcut_fallback()
        elif self._shortcut_fallback is not None:
            self._shortcut_fallback.stop()
        self._notify_shortcut_status()

    def _notify_shortcut_status(self) -> None:
        for listener in tuple(self._shortcut_status_listeners):
            listener()
