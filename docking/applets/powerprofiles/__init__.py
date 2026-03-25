"""Public package surface for the Power Profiles applet.

This package keeps the import surface intentionally small while making the
implementation split explicit. In the standard Docking applet layout:

- ``applet.py`` owns GTK lifecycle and user interaction,
- ``render.py`` owns dock-icon drawing,
- ``state.py`` owns pure logic or platform-facing helpers.

Re-exporting ``PowerProfilesApplet`` here gives the catalog, tests, and documentation a
simple import path without turning the package ``__init__`` into an alternate
implementation layer.
"""

from .applet import PowerProfilesApplet
from .render import create_power_profiles_icon
from .state import (
    NullPowerProfilesBackend,
    PowerProfilesBackend,
    PowerProfilesControlBackend,
    PowerProfilesState,
    TlpBackend,
    TunedBackend,
    detect_backend,
    normalize_profile,
    order_profiles,
    profile_label,
    tooltip_text,
    unavailable_state,
)

__all__ = [
    "NullPowerProfilesBackend",
    "PowerProfilesApplet",
    "PowerProfilesBackend",
    "PowerProfilesControlBackend",
    "PowerProfilesState",
    "TlpBackend",
    "TunedBackend",
    "create_power_profiles_icon",
    "detect_backend",
    "normalize_profile",
    "order_profiles",
    "profile_label",
    "tooltip_text",
    "unavailable_state",
]
