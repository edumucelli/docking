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

"""Pure state helpers for Brightness applet -- no GTK dependency."""

from __future__ import annotations

from pathlib import Path

from docking.log import get_logger

log = get_logger(name="brightness.state")

_BACKLIGHT_DIR = Path("/sys/class/backlight")


def brightness_icon_name(brightness: float) -> str:
    """Map brightness level to FreeDesktop icon name."""
    if brightness <= 0.3:
        return "display-brightness-low-symbolic"
    if brightness <= 0.7:
        return "display-brightness-medium-symbolic"
    return "display-brightness-symbolic"


STEP = 0.02
