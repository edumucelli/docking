# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

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
