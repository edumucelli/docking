"""Pure state logic for Recent Files applet."""

from __future__ import annotations

from typing import NamedTuple

MAX_ENTRIES = 15
MAX_LABEL_LEN = 40


class RecentEntry(NamedTuple):
    name: str
    uri: str


def truncate_name(text: str) -> str:
    """Truncate display name with ellipsis if too long."""
    if len(text) <= MAX_LABEL_LEN:
        return text
    return text[: MAX_LABEL_LEN - 1] + "\u2026"


def tooltip_text(entries: list[RecentEntry]) -> str:
    """Tooltip showing most recent file name or fallback."""
    if not entries:
        return "No recent files"
    return entries[0].name
