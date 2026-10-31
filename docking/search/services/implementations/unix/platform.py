"""Existing portal integration and environment-dependent X11 fallback."""

from __future__ import annotations

from collections.abc import Callable

from docking.search.services.platform import SearchIntegration

from ...global_shortcuts import (
    ActivationCallback,
    GlobalShortcutService,
    StatusCallback,
)
from ...platform import ShortcutFallback
from .shortcuts import GlobalShortcutsService
from .x11_shortcuts import X11GlobalShortcutService, is_x11_session


class UnixSearchIntegration(SearchIntegration):
    def primary(
        self,
        *,
        app_id: str,
        preferred_trigger: str,
        on_activated: ActivationCallback,
        on_status_changed: StatusCallback,
    ) -> GlobalShortcutService:
        return GlobalShortcutsService(
            app_id=app_id,
            preferred_trigger=preferred_trigger,
            on_activated=on_activated,
            on_status_changed=on_status_changed,
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
