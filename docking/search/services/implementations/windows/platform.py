"""Native Search shortcut composition without importing portal/X11 transports."""

from __future__ import annotations

from collections.abc import Callable

from docking.search.services.platform import SearchIntegration
from docking.search.services.window_focus import WindowFocus

from ...global_shortcuts import (
    ActivationCallback,
    GlobalShortcutService,
    StatusCallback,
)
from ...platform import ShortcutFallback
from .shortcuts import WindowsGlobalShortcutsService


class WindowsSearchIntegration(SearchIntegration):
    @property
    def opaque_action_panel(self) -> bool:
        return True

    def focus_factory(self) -> Callable[[], WindowFocus] | None:
        from .focus import WindowsSearchFocus

        return WindowsSearchFocus

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
    ) -> GlobalShortcutService:
        return WindowsGlobalShortcutsService(
            preferred_trigger=preferred_trigger,
            trigger_description=trigger_description,
            schedule_idle=schedule_idle,
            remove_source=remove_source,
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
        return None
