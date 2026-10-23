# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Shared icon preference values.

Docking has several icon-selection surfaces: applet menus, file/folder item
menus, config persistence, and renderer decisions. Keep the persisted source
values in core so those layers agree without importing UI or applet modules
from each other.
"""

from __future__ import annotations

from enum import Enum


class IconSource(str, Enum):
    """Supported user-selectable icon sources.

    There is no separate "default" value. Callers should omit the preference
    or fall back to `DOCKING` when a persisted value is missing/invalid.
    """

    DOCKING = "docking"
    SYSTEM = "system"
    CUSTOM = "custom"


ICON_SOURCE_PREF_KEY = "icon_source"
CUSTOM_ICON_PATH_KEY = "custom_icon_path"


def icon_source_from_value(value: object) -> IconSource | None:
    """Parse a persisted icon source value."""
    if isinstance(value, IconSource):
        return value
    if not isinstance(value, str):
        return None
    try:
        return IconSource(value)
    except ValueError:
        return None
