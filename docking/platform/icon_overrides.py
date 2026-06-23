# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

"""Preference helpers for per-item custom dock icons."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from docking.core.icons import (
    CUSTOM_ICON_PATH_KEY,
    ICON_SOURCE_PREF_KEY,
    IconSource,
    icon_source_from_value,
)

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem


def item_icon_key(item: DockItem) -> str:
    """Return the stable preference key for an item's icon override."""
    return item.prefs_key or item.target or item.desktop_id


def item_icon_prefs(config: Config, item: DockItem) -> dict[str, Any]:
    """Return a mutable copy of an item's icon preference map."""
    key = item_icon_key(item=item)
    stored = config.item_prefs.get(key, {})
    return dict(stored) if isinstance(stored, dict) else {}


def custom_icon_path(config: Config, item: DockItem) -> Path | None:
    """Return a configured custom icon path for an item, if active."""
    prefs = item_icon_prefs(config=config, item=item)
    if icon_source_from_value(prefs.get(ICON_SOURCE_PREF_KEY)) is not IconSource.CUSTOM:
        return None
    raw_path = prefs.get(CUSTOM_ICON_PATH_KEY)
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return None
    return path


def set_custom_icon(config: Config, item: DockItem, path: Path) -> None:
    """Persist a custom icon path for an item."""
    key = item_icon_key(item=item)
    prefs = item_icon_prefs(config=config, item=item)
    prefs[ICON_SOURCE_PREF_KEY] = IconSource.CUSTOM.value
    prefs[CUSTOM_ICON_PATH_KEY] = str(path)
    config.item_prefs[key] = prefs


def reset_custom_icon(config: Config, item: DockItem) -> None:
    """Clear a custom icon path for an item, preserving unrelated preferences."""
    key = item_icon_key(item=item)
    prefs = item_icon_prefs(config=config, item=item)
    prefs.pop(ICON_SOURCE_PREF_KEY, None)
    prefs.pop(CUSTOM_ICON_PATH_KEY, None)
    if prefs:
        config.item_prefs[key] = prefs
    else:
        config.item_prefs.pop(key, None)
