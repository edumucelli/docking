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

"""State and grouping logic for Applications applet."""

from __future__ import annotations

from collections import defaultdict

from docking.platform.applications.listing import (
    ApplicationListing,
    listing_categories,
    listing_name,
    visible_listings,
)
from docking.platform.applications.registry import ApplicationRegistry

# FreeDesktop main categories -> display label
CATEGORY_LABELS: dict[str, str] = {
    "AudioVideo": "Multimedia",
    "Audio": "Multimedia",
    "Video": "Multimedia",
    "Development": "Development",
    "Education": "Education",
    "Game": "Games",
    "Graphics": "Graphics",
    "Network": "Internet",
    "Office": "Office",
    "Science": "Science",
    "Settings": "Settings",
    "System": "System",
    "Utility": "Accessories",
}

# Category -> icon name for submenu
CATEGORY_ICONS: dict[str, str] = {
    "Multimedia": "applications-multimedia",
    "Development": "applications-development",
    "Education": "applications-science",
    "Games": "applications-games",
    "Graphics": "applications-graphics",
    "Internet": "applications-internet",
    "Office": "applications-office",
    "Science": "applications-science",
    "Settings": "preferences-system",
    "System": "applications-system",
    "Accessories": "applications-utilities",
}


def _map_category(categories: str) -> str:
    """Map FreeDesktop category string to display category."""
    display_cat = "Other"
    for raw_cat in categories.split(";"):
        if raw_cat in CATEGORY_LABELS:
            display_cat = CATEGORY_LABELS[raw_cat]
            break
    return display_cat


def _build_app_categories(
    registry: ApplicationRegistry | None = None,
) -> dict[str, list[ApplicationListing]]:
    """Group installed apps by FreeDesktop category.

    Returns {display_category: [app_info, ...]} sorted by app name.
    Apps that don't match any known category go into "Other".
    Hidden and no-display apps are excluded.
    """
    categories: dict[str, list[ApplicationListing]] = defaultdict(list)

    for app_info in visible_listings(registry):
        cats = listing_categories(app_info)
        categories[_map_category(categories=cats)].append(app_info)

    # Sort apps within each category by display name
    for apps in categories.values():
        apps.sort(key=lambda app: listing_name(app).lower())

    return dict(categories)
