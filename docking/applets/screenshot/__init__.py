"""Screenshot applet package."""

from .applet import ScreenshotApplet
from .state import _TOOLS, Tool, _detect_tool, _run

__all__ = [
    "_TOOLS",
    "ScreenshotApplet",
    "Tool",
    "_detect_tool",
    "_run",
]
