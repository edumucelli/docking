"""Small derived values shared by application consumers."""

from __future__ import annotations

from .constants import FALLBACK_ICON
from .types import ActionSource, ApplicationAction, ApplicationInfo, ApplicationOrigin

NEW_WINDOW_ACTION_ID = "new-window"


def dock_icon_name(info: ApplicationInfo) -> str:
    """Return the declared icon or the dock's generic fallback."""
    if (
        info.origin is ApplicationOrigin.GENERATED
        and info.declared_icon in {"", FALLBACK_ICON}
        and info.executable_path is not None
    ):
        executable = info.executable_path
        stem = (
            executable.name[: -len(".AppImage")]
            if executable.name.lower().endswith(".appimage")
            else executable.stem
            if executable.suffix
            else executable.name
        )
        for suffix in (".svg", ".png", ".xpm"):
            candidate = executable.with_name(f"{stem}{suffix}")
            if candidate.is_file():
                try:
                    return str(candidate.resolve(strict=True))
                except (OSError, RuntimeError):
                    continue
        if executable.name.lower().endswith(".appimage"):
            return "application-x-appimage"
    return info.declared_icon or FALLBACK_ICON


def quicklist_actions(info: ApplicationInfo) -> tuple[ApplicationAction, ...]:
    """Preserve the dock's source-exclusive quicklist behavior."""
    source = ActionSource.GIO if info.has_gio_source else ActionSource.DESKTOP_FILE
    return tuple(action for action in info.actions if source in action.sources)


def new_window_action(info: ApplicationInfo) -> ApplicationAction | None:
    """Return the Gio-routable new-window action, when present."""
    if not info.has_gio_source:
        return None
    for action in info.actions:
        if (
            action.action_id == NEW_WINDOW_ACTION_ID
            and ActionSource.GIO in action.sources
        ):
            return action
    return None


__all__ = [
    "FALLBACK_ICON",
    "NEW_WINDOW_ACTION_ID",
    "dock_icon_name",
    "new_window_action",
    "quicklist_actions",
]
