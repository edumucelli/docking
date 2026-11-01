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

"""Portable state and presentation helpers for Power Profiles."""

from __future__ import annotations

from dataclasses import dataclass

from docking.i18n import _

PROFILES = ("power-saver", "balanced", "performance")


@dataclass(frozen=True, slots=True)
class PowerProfilesState:
    """Small immutable state snapshot consumed by the applet/presentation layer."""

    available: bool
    active_profile: str
    profiles: tuple[str, ...]
    degraded_reason: str = ""
    error: str = ""
    power_source: str = ""


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
    preferred = PROFILES
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
    if state.power_source:
        lines.append(f"Power source: {state.power_source}")
    if state.degraded_reason:
        lines.append(f"Limited: {state.degraded_reason}")
    return "\n".join(lines)
