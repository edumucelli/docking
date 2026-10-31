# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""State and backend helpers for Power Profiles applet.

This module intentionally contains most of the applet's complexity so that:
- GTK/UI code can stay small and predictable.
- backend-specific quirks can be tested in isolation.
- profile naming/mapping behavior is centralized in one place.

Why there are multiple backends
===============================

Linux power-profile control is fragmented. Different distros and setups expose
different tools:

1) power-profiles-daemon (PPD), via DBus:
   - service: ``net.hadess.PowerProfiles``
   - canonical and preferred implementation
   - supports "performance / balanced / power-saver" directly

2) tuned-adm:
   - profile system with many profile names (e.g. ``throughput-performance``)
   - requires profile-name mapping to canonical profiles

3) tlp:
   - not profile-based in the same way
   - mostly "AC mode" vs "Battery mode", so we map commands to canonical
     profiles as best effort:
       - power-saver -> ``tlp bat``
       - performance -> ``tlp ac``
       - balanced -> ``tlp start``

The detection chain is intentionally ordered from most semantically accurate to
least:

    power-profiles-daemon -> tuned-adm -> tlp -> null backend

The applet consumes only two backend operations:
- ``get_state()``: snapshot for icon/menu/tooltip
- ``set_active_profile(profile)``: request profile switch

That uniform contract makes it straightforward to add future backends or remove
legacy ones without changing applet UI wiring.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from docking.applets.powerprofiles import meta
from docking.i18n import _
from docking.log import get_logger, with_context

log = with_context(
    get_logger(name="powerprofiles"),
    applet_id=meta.id,
)

SERVICE = "net.hadess.PowerProfiles"
PATH = "/net/hadess/PowerProfiles"
POWER_PROFILES_IFACE = "net.hadess.PowerProfiles"
PROPERTIES_IFACE = "org.freedesktop.DBus.Properties"
DBUS_SERVICE = "org.freedesktop.DBus"
DBUS_PATH = "/org/freedesktop/DBus"
DBUS_IFACE = "org.freedesktop.DBus"
DBUS_NAME_HAS_OWNER_TIMEOUT_MS = 1200
DBUS_GET_ALL_TIMEOUT_MS = 2000
DBUS_PROPERTY_SET_TIMEOUT_MS = 1800
TUNED_SET_TIMEOUT_S = 4.0
TUNED_ACTIVE_TIMEOUT_S = 3.0
TUNED_LIST_TIMEOUT_S = 3.0
TLP_STATUS_TIMEOUT_S = 3.0
TLP_SET_TIMEOUT_S = 4.0
DEFAULT_COMMAND_TIMEOUT_S = 2.5


@dataclass(frozen=True, slots=True)
class PowerProfilesState:
    """Small immutable state snapshot consumed by the applet/presentation layer."""

    available: bool
    active_profile: str
    profiles: tuple[str, ...]
    degraded_reason: str = ""
    error: str = ""


def unavailable_state(error: str = "") -> PowerProfilesState:
    """Construct a standardized unavailable-state payload."""
    return PowerProfilesState(
        available=False,
        active_profile="",
        profiles=(),
        error=error,
    )


def normalize_profile(profile: str) -> str:
    """Normalize profile aliases/spellings into canonical profile IDs."""
    raw = profile.strip().lower().replace("_", "-")
    if raw in {"power saver", "power-save", "powersave", "power-saver"}:
        return "power-saver"
    if raw in {"balanced", "balance"}:
        return "balanced"
    if raw in {"performance", "perf"}:
        return "performance"
    return raw


def profile_label(profile: str) -> str:
    """Convert canonical profile IDs into user-facing labels."""
    normalized = normalize_profile(profile)
    if normalized == "power-saver":
        return _("Power Saver")
    if normalized == "balanced":
        return _("Balanced")
    if normalized == "performance":
        return _("Performance")
    if not normalized:
        return _("Unknown")
    return normalized.replace("-", " ").title()


def order_profiles(profiles: tuple[str, ...]) -> tuple[str, ...]:
    """Return deterministic profile order for menu rendering.

    Canonical order is tuned for UX:
    - power-saver first
    - balanced second
    - performance third

    Unknown profiles are preserved and appended afterwards.
    """
    preferred = ("power-saver", "balanced", "performance")
    normalized = [normalize_profile(profile) for profile in profiles if profile]
    seen: set[str] = set()
    ordered: list[str] = []
    for profile in preferred:
        if profile in normalized and profile not in seen:
            ordered.append(profile)
            seen.add(profile)
    for profile in normalized:
        if profile not in seen:
            ordered.append(profile)
            seen.add(profile)
    return tuple(ordered)


def tooltip_text(state: PowerProfilesState) -> str:
    """Build multi-line tooltip content from state snapshot."""
    if not state.available:
        if state.error:
            return _("Power Profiles unavailable") + f"\n{state.error}"
        return _("Power Profiles unavailable")

    lines = [
        "Power Profiles",
        f"Current: {profile_label(state.active_profile)}",
    ]
    if state.profiles:
        names = ", ".join(profile_label(profile) for profile in state.profiles)
        lines.append(f"Available: {names}")
    if state.degraded_reason:
        lines.append(f"Limited: {state.degraded_reason}")
    return "\n".join(lines)


def _unpack(value: Any) -> Any:
    """Recursively unpack GLib variant-like objects into native Python values."""
    if hasattr(value, "unpack"):
        try:
            return _unpack(value.unpack())
        except Exception as exc:
            log.debug("Failed to unpack GLib variant-like value: %s", exc)
            return value
    if isinstance(value, dict):
        return {k: _unpack(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return tuple(_unpack(v) for v in value)
    if isinstance(value, list):
        return [_unpack(v) for v in value]
    return value


def _as_str(value: Any) -> str:
    """String coercion helper after variant unpacking."""
    unpacked = _unpack(value)
    if unpacked is None:
        return ""
    return str(unpacked)
