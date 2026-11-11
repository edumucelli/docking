"""Existing portal integration and environment-dependent X11 fallback."""

from __future__ import annotations

from collections.abc import Callable

from docking.search.services.platform import SearchIntegration, WindowFocus

from ...global_shortcuts import (
    ActivationCallback,
    GlobalShortcutService,
    GlobalShortcutSpec,
    StatusCallback,
)
from ...platform import ShortcutFallback
from .shortcut_session import PortalShortcutSession
from .x11_shortcuts import X11GlobalShortcutService, is_x11_session


class UnixSearchIntegration(SearchIntegration):
    def __init__(self) -> None:
        self._shortcuts = PortalShortcutSession()

    @property
    def opaque_action_panel(self) -> bool:
        return False

    def create_focus(self) -> WindowFocus | None:
        return None

    def primary(
        self,
        *,
        app_id: str,
        preferred_trigger: str,
        on_activated: ActivationCallback,
        on_status_changed: StatusCallback,
        trigger_description: str,
        schedule_idle: Callable[..., int],
        remove_source: Callable[[int], bool],
        shortcut_id: str = "toggle-search",
        description: str = "Toggle Docking search",
    ) -> GlobalShortcutService:
        return self._shortcuts.registration(
            app_id=app_id,
            spec=GlobalShortcutSpec(shortcut_id, description, preferred_trigger),
            on_activated=on_activated,
            on_status_changed=on_status_changed,
            schedule_idle=schedule_idle,
            remove_source=remove_source,
        )

    def fallback(
        self,
        *,
        shortcut: str,
        on_activated: Callable[[int], None],
        schedule_idle: Callable[..., int],
    ) -> ShortcutFallback | None:
        if not is_x11_session():
            return None
        return X11GlobalShortcutService(
            shortcut=shortcut, on_activated=on_activated, schedule_idle=schedule_idle
        )
