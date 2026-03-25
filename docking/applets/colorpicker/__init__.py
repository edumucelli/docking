"""Public package surface for the Color Picker applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``ColorPickerApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from .applet import ColorPickerApplet
from .state import pick_pixel, rgb_to_hex

__all__ = ["ColorPickerApplet", "pick_pixel", "rgb_to_hex"]
