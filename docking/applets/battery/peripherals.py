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

"""Shared peripheral battery state and tooltip formatting."""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple

from docking.i18n import _


class PeripheralBattery(NamedTuple):
    """Charge info for one wireless accessory."""

    model: str
    kind: str  # Human-readable device type, e.g. "Keyboard"
    percent: float | None
    charging: bool
    level: str  # Coarse level word when no exact percent, else ""


def peripheral_label(peripheral: PeripheralBattery) -> str:
    """Menu/tooltip row text, e.g. ``Keychron K8: 96%``."""
    name = peripheral.model or peripheral.kind
    charge = _charge_text(peripheral=peripheral)
    return f"{name}: {charge}" if charge else name


def tooltip_lines(
    *, battery_line: str, peripherals: Iterable[PeripheralBattery]
) -> str:
    """Compose the battery tooltip with one peripheral row per line beneath it."""
    lines = [battery_line]
    lines.extend(peripheral_label(peripheral=peripheral) for peripheral in peripherals)
    return "\n".join(lines)


def _charge_text(*, peripheral: PeripheralBattery) -> str:
    # UPower can supply zero alongside a coarse level. Preserve that label,
    # while retaining an exact zero when no coarse level is available.
    if peripheral.percent is not None and (
        peripheral.percent > 0 or (peripheral.percent == 0 and not peripheral.level)
    ):
        text = f"{peripheral.percent:.0f}%"
    elif peripheral.level:
        text = peripheral.level
    else:
        return ""
    if peripheral.charging:
        text = _("{charge} (charging)").format(charge=text)
    return text
