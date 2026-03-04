"""Power Profiles applet package."""

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
    "PowerProfilesApplet",
    "NullPowerProfilesBackend",
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
