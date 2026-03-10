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
