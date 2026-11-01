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

"""Pure screenshot modes and owned pixel results."""

from dataclasses import dataclass
from typing import Literal

Mode = Literal["full", "window", "region"]


@dataclass(frozen=True)
class SelectedRegion:
    """GTK logical coordinates retain subpixel positions on scaled displays."""

    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True)
class ScreenshotImage:
    pixels: bytes
    width: int
    height: int
    alpha: bool = False

    def __post_init__(self) -> None:
        channels = 4 if self.alpha else 3
        if (
            not 0 < self.width <= 32768
            or not 0 < self.height <= 32768
            or self.width * self.height * channels > 128 * 1024 * 1024
            or len(self.pixels) != self.width * self.height * channels
        ):
            raise ValueError("Invalid screenshot pixels")


@dataclass(frozen=True)
class ScreenshotResult:
    accepted: bool
    image: ScreenshotImage | None = None
