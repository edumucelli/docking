"""Clippy applet public API."""

from .applet import ClippyApplet
from .state import _truncate

__all__ = ["ClippyApplet", "_truncate"]
