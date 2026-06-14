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

"""Applet identity, metadata, and menu-grouping helpers.

Applets appear in several places at once: persisted dock configuration, runtime
item models, settings menus, catalog lookups, and user-facing grouping in the
applet picker. Those layers need one shared notion of "which applet is this?".

The ``applet://`` desktop-id scheme

Docking models applets alongside launchers and files, so applets need a desktop
id that can live in the same pinned-item list. The custom ``applet://`` prefix
creates that shared namespace while still making applets easy to recognize.
Optional ``#<instance>`` suffixes let the same applet type appear multiple
times when the UX allows it, such as separators.

What this module owns

- ``AppletMeta`` dataclass declared by each applet package,
- ``AppletCategory`` enum for menu grouping,
- parser/builders for ``applet://...`` desktop ids.

Each applet's identity is defined in its own ``__init__.py`` via an
``AppletMeta`` instance. This module provides the shared types and helpers
that make that work.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

AppletId = str
"""Type alias for applet identifiers (e.g. ``"clock"``, ``"separator"``)."""

APPLET_PREFIX = "applet://"

log = logging.getLogger(__name__)


class AppletCategory(str, Enum):
    LAUNCHER = "Launcher & Navigation"
    PRODUCTIVITY = "Time & Productivity"
    SYSTEM = "System & Power"
    WELLNESS = "Wellness & Ambient"
    INFORMATION = "Information and Environment"
    OTHER = "Other"


APPLET_CATEGORY_ORDER: tuple[AppletCategory, ...] = (
    AppletCategory.LAUNCHER,
    AppletCategory.PRODUCTIVITY,
    AppletCategory.SYSTEM,
    AppletCategory.WELLNESS,
    AppletCategory.INFORMATION,
    AppletCategory.OTHER,
)


@dataclass(frozen=True, slots=True)
class AppletMeta:
    """Lightweight metadata declared in each applet's ``__init__.py``."""

    id: AppletId
    name: str
    category: AppletCategory


def parse_applet_id(desktop_id: str) -> str | None:
    """Parse ``applet://...`` desktop ids into an applet id string.

    Supports instance suffixes like ``applet://separator#2``.
    Returns None for non-applet desktop IDs.
    """
    if not desktop_id.startswith(APPLET_PREFIX):
        return None
    raw = desktop_id[len(APPLET_PREFIX) :]
    raw_id = raw.split("#", 1)[0]
    return raw_id if raw_id else None


def applet_id_from(desktop_id: str) -> str:
    """Extract applet id from desktop id.

    Raises ValueError for non-applet ids. Returns the id string even for
    applets not currently in the catalog (they may fail to load later).
    """
    parsed = parse_applet_id(desktop_id=desktop_id)
    if parsed is None:
        raise ValueError(f"Invalid applet desktop id: {desktop_id}")
    return parsed


def is_applet_desktop_id(desktop_id: str) -> bool:
    """True if desktop_id has applet prefix."""
    return desktop_id.startswith(APPLET_PREFIX)


def applet_desktop_id(applet_id: str, instance: int | None = None) -> str:
    """Build a canonical applet desktop id, optionally with instance suffix."""
    if instance is None:
        return f"{APPLET_PREFIX}{applet_id}"
    return f"{APPLET_PREFIX}{applet_id}#{instance}"


def category_for(applet_id: str) -> AppletCategory:
    """Resolve applet category for menu grouping."""
    from docking.applets import get_applet_catalog

    meta = get_applet_catalog().get(applet_id)
    if meta is None:
        log.warning("No metadata for applet %s, falling back to OTHER", applet_id)
        return AppletCategory.OTHER
    return AppletCategory(meta.category)
