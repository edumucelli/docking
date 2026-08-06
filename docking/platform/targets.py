"""Generic file, folder, and URI target operations."""

from __future__ import annotations

from docking.platform.launcher import (
    FileTargetInfo,
    normalize_file_target,
    open_target,
    resolve_file,
)

__all__ = ["FileTargetInfo", "normalize_file_target", "open_target", "resolve_file"]
