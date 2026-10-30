"""Select Search transports behind their shared lifecycle contracts."""

from __future__ import annotations

from collections.abc import Callable

from docking.platform.environment import is_windows
from docking.search.app_identity import application_id

from .global_shortcuts import (
    ActivationCallback,
    SearchShortcutService,
    ShortcutFallback,
    StatusCallback,
)
from .window_focus import WindowFocus


def create_focus_factory() -> Callable[[], WindowFocus] | None:
    if not is_windows():
        return None
    from .windows_focus import WindowsSearchFocus

    return WindowsSearchFocus


def create_shortcut_service(
    *,
    preferred_trigger: str,
    trigger_description: str,
    on_activated: ActivationCallback,
    on_status_changed: StatusCallback,
    schedule_idle: Callable[..., int],
    remove_source: Callable[[int], bool],
) -> SearchShortcutService:
    if is_windows():
        from .windows_shortcuts import WindowsGlobalShortcutsService

        return WindowsGlobalShortcutsService(
            preferred_trigger=preferred_trigger,
            trigger_description=trigger_description,
            on_activated=on_activated,
            on_status_changed=on_status_changed,
            schedule_idle=schedule_idle,
            remove_source=remove_source,
        )
    from .global_shortcuts import GlobalShortcutsService

    return GlobalShortcutsService(
        app_id=application_id(),
        preferred_trigger=preferred_trigger,
        on_activated=on_activated,
        on_status_changed=on_status_changed,
    )


def create_shortcut_fallback(
    *,
    shortcut: str,
    on_activated: Callable[[int], None],
    schedule_idle: Callable[..., int],
) -> ShortcutFallback | None:
    if is_windows():
        return None
    from .x11_shortcuts import X11GlobalShortcutService, is_x11_session

    if not is_x11_session():
        return None
    return X11GlobalShortcutService(
        shortcut=shortcut,
        on_activated=on_activated,
        schedule_idle=schedule_idle,
    )
