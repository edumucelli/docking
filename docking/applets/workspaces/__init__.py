"""Workspaces applet package."""

from .applet import Gtk, Wnck, WorkspacesApplet  # noqa: F401
from .render import _render_grid

__all__ = [
    "WorkspacesApplet",
    "_render_grid",
]
