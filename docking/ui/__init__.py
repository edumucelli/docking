"""Lazy UI package exports.

Keep package import cheap so modules like ``docking.ui.about`` can be imported
without pulling in the full dock window/runtime surface.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DockRenderer", "DockWindow"]


def __getattr__(name: str) -> Any:
    if name == "DockWindow":
        from docking.ui.dock_window import DockWindow

        return DockWindow
    if name == "DockRenderer":
        from docking.ui.renderer import DockRenderer

        return DockRenderer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
