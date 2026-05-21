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

"""Small numeric helpers shared by rendering and state code."""

from __future__ import annotations


def clamp(value: float, minimum: float, maximum: float) -> float:
    """Return value constrained to the inclusive minimum..maximum range."""
    return max(minimum, min(maximum, value))


def clamp_int(value: int, minimum: int, maximum: int) -> int:
    """Return an integer value constrained to the inclusive range."""
    return max(minimum, min(maximum, value))


def clamp_index(index: int, length: int) -> int:
    """Return an index constrained to the valid range for a sequence length."""
    if length <= 0:
        return 0
    return clamp_int(index, 0, length - 1)
