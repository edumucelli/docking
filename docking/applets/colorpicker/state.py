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

"""Pure helpers for Color Picker applet."""

from __future__ import annotations

import math

_HEX_DIGITS = set("0123456789abcdefABCDEF")


def rgb_to_hex(r: int, g: int, b: int) -> str:
    """Convert RGB to uppercase hex string."""
    return f"#{r:02X}{g:02X}{b:02X}"


def coerce_channel(value: object, *, default: float) -> float:
    """Coerce a persisted colour channel into 0.0-1.0.

    The value reaches cairo's ``set_source_rgb`` during construction, where
    anything that is not a real number raises.
    """
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    return min(1.0, max(0.0, parsed))


def coerce_hex(value: object) -> str:
    """Return a usable ``#RRGGBB`` label, or an empty string.

    The value is rendered in a label and a menu item, so it has to be text.
    """
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) != 7 or not text.startswith("#"):
        return ""
    if not all(char in _HEX_DIGITS for char in text[1:]):
        return ""
    return text.upper()
