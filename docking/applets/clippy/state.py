"""Pure state helpers for Clippy applet."""

from __future__ import annotations

from docking.i18n import _

MAX_DISPLAY_LEN = 50


def _truncate(text: str, max_len: int = MAX_DISPLAY_LEN) -> str:
    """Truncate text for menu display, replacing newlines with spaces."""
    clean = text.replace("\n", " ").replace("\t", " ").strip()
    if len(clean) > max_len:
        return clean[:max_len] + "..."
    return clean


def tooltip_text(clips: list[str], cur_position: int) -> str:
    """Build tooltip from clip history and current cursor position."""
    if clips and 0 < cur_position <= len(clips):
        return _truncate(clips[cur_position - 1])
    return _("Clippy (empty)")


def add_clip(clips: list[str], text: str, max_entries: int) -> tuple[list[str], int]:
    """Add clip to history (dedup, cap) and return updated list/position."""
    next_clips = list(clips)
    if text in next_clips:
        next_clips.remove(text)
    next_clips.append(text)
    while len(next_clips) > max_entries:
        next_clips.pop(0)
    return next_clips, len(next_clips)


def cycle_position(clips_len: int, cur_position: int, direction_up: bool) -> int:
    """Cycle cursor position through history with wraparound."""
    if clips_len <= 0:
        return 0
    if direction_up:
        next_pos = cur_position - 1
        if next_pos < 1:
            next_pos = clips_len
        return next_pos

    next_pos = cur_position + 1
    if next_pos > clips_len:
        next_pos = 1
    return next_pos
