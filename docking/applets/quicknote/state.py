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

"""Pure logic for quick note applet -- no GTK dependency."""

from __future__ import annotations

MAX_TOOLTIP_LEN = 80


def tooltip_text(*, note: str) -> str:
    """Return truncated preview or placeholder for empty notes."""
    stripped = note.strip()
    if not stripped:
        return "Empty note"
    first_line = stripped.split("\n", 1)[0]
    if len(first_line) > MAX_TOOLTIP_LEN:
        return first_line[:MAX_TOOLTIP_LEN] + "..."
    return first_line


def prefs_from_note(*, note: str) -> dict[str, str]:
    """Serialize note to preferences dict."""
    return {"note": note}


def note_from_prefs(prefs: dict | None) -> str:
    """Load note text from preferences, default empty."""
    if not prefs:
        return ""
    return prefs.get("note", "")
