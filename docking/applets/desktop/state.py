"""Pure state helpers for Desktop applet."""

from __future__ import annotations


def next_showing_desktop(current: bool) -> bool:
    """Return toggled showing-desktop state."""
    return not current
