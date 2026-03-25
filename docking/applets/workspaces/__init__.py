"""Public package surface for the Workspaces applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``WorkspacesApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from .applet import Gtk, Wnck, WorkspacesApplet  # noqa: F401
from .render import _render_grid

__all__ = [
    "WorkspacesApplet",
    "_render_grid",
]
