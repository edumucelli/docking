"""State helpers for workspaces applet."""

from __future__ import annotations

import re

DEFAULT_WORKSPACES_COUNT = 4
_GENERIC_WS_NAME_RE = re.compile(r"^Workspace\s+\d+$")


def active_workspace_number(*, active_number: int | None) -> int:
    return active_number if active_number is not None else -1


def workspace_count(*, count: int | None) -> int:
    if count is None or count <= 0:
        return DEFAULT_WORKSPACES_COUNT
    return count


def next_workspace_index(*, current: int, count: int, delta: int) -> int:
    if count <= 0:
        return 0
    return (current + delta) % count


def workspace_label(*, name: str | None, number: int) -> str:
    base = f"Workspace {number + 1}"
    if name:
        clean = name.strip()
        if clean and clean != base and not _GENERIC_WS_NAME_RE.match(clean):
            return f"{base}: {clean}"
    return base
