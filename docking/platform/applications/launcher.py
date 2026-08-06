"""Application launching backed by the canonical registry."""

from __future__ import annotations

from docking.platform.launcher import (
    FileTargetInfo,
    Launcher,
    fallback_file_icon_name,
    get_actions,
    launch,
    launch_action,
    launch_new_window,
    normalize_file_target,
    open_target,
)

__all__ = [
    "FileTargetInfo",
    "Launcher",
    "fallback_file_icon_name",
    "get_actions",
    "launch",
    "launch_action",
    "launch_new_window",
    "normalize_file_target",
    "open_target",
]
