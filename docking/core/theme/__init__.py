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

"""Public theme API."""

from docking.core.theme.migration import (
    DEPRECATED_THEME_KEYS,
    ThemeMigrationChange,
    ThemeMigrationResult,
    migrate_theme_dict,
)
from docking.core.theme.theme import (
    _BUILTIN_THEMES_DIR,
    _USER_THEME_TEMPLATE_NAME,
    RGB,
    RGBA,
    IndicatorStyle,
    Theme,
    _rgba,
    ensure_user_theme_template,
    list_theme_names,
    user_themes_dir,
)

__all__ = [
    "DEPRECATED_THEME_KEYS",
    "RGB",
    "RGBA",
    "_BUILTIN_THEMES_DIR",
    "_USER_THEME_TEMPLATE_NAME",
    "IndicatorStyle",
    "Theme",
    "ThemeMigrationChange",
    "ThemeMigrationResult",
    "_rgba",
    "ensure_user_theme_template",
    "list_theme_names",
    "migrate_theme_dict",
    "user_themes_dir",
]
