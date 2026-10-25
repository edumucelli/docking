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

"""Platform-independent brightness state and presentation helpers."""

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BrightnessState:
    level: float | None = None
    # Opaque display identity returned by the backend. Writes must name the
    # same target so a queued scroll cannot reach a different primary monitor.
    target: str = ""
    # Empty means continuous control. Discrete devices report their usable
    # levels so a small scroll can advance even on coarse backlight hardware.
    levels: tuple[float, ...] = ()

    @property
    def available(self) -> bool:
        return (
            bool(self.target) and self.level is not None and math.isfinite(self.level)
        )


def brightness_icon_name(brightness: float) -> str:
    """Map brightness level to FreeDesktop icon name."""
    if brightness <= 0.3:
        return "display-brightness-low-symbolic"
    if brightness <= 0.7:
        return "display-brightness-medium-symbolic"
    return "display-brightness-symbolic"


STEP = 0.02
