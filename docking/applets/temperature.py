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

"""Shared temperature display helpers for applets."""

from __future__ import annotations

from enum import Enum

from docking.i18n import _


class TemperatureUnit(str, Enum):
    """Supported temperature display units."""

    CELSIUS = "celsius"
    FAHRENHEIT = "fahrenheit"


def normalize_temperature_unit(value: object) -> TemperatureUnit:
    """Return a known temperature unit, defaulting to Celsius."""
    if isinstance(value, TemperatureUnit):
        return value
    text = str(value or "").strip().lower()
    if text in {"fahrenheit", "f", "farenheit"}:
        return TemperatureUnit.FAHRENHEIT
    return TemperatureUnit.CELSIUS


def temperature_unit_label(temperature_unit: TemperatureUnit | object) -> str:
    """Return the user-facing menu label for a temperature unit."""
    unit = normalize_temperature_unit(temperature_unit)
    if unit == TemperatureUnit.FAHRENHEIT:
        return _("Fahrenheit")
    return _("Celsius")


def format_temperature(
    celsius: float | None,
    *,
    temperature_unit: TemperatureUnit | object = TemperatureUnit.CELSIUS,
    precision: int = 0,
) -> str:
    """Format a full temperature value for tooltips and menus."""
    if celsius is None:
        return "--"
    unit = normalize_temperature_unit(temperature_unit)
    value = temperature_value(celsius=celsius, temperature_unit=unit)
    decimals = max(0, precision)
    return f"{value:.{decimals}f}\N{DEGREE SIGN}{temperature_suffix(unit)}"


def format_temperature_compact(
    celsius: float | None,
    *,
    temperature_unit: TemperatureUnit | object = TemperatureUnit.CELSIUS,
) -> str:
    """Format the tiny icon label: rounded value plus degree sign."""
    if celsius is None:
        return "--"
    unit = normalize_temperature_unit(temperature_unit)
    value = temperature_value(celsius=celsius, temperature_unit=unit)
    return f"{value:.0f}\N{DEGREE SIGN}"


def format_temperature_range(
    *,
    low_celsius: float,
    high_celsius: float,
    temperature_unit: TemperatureUnit | object = TemperatureUnit.CELSIUS,
    precision: int = 0,
) -> str:
    """Format a min/max temperature range in the selected unit."""
    unit = normalize_temperature_unit(temperature_unit)
    low = temperature_value(celsius=low_celsius, temperature_unit=unit)
    high = temperature_value(celsius=high_celsius, temperature_unit=unit)
    decimals = max(0, precision)
    return (
        f"{low:.{decimals}f}/{high:.{decimals}f}"
        f"\N{DEGREE SIGN}{temperature_suffix(unit)}"
    )


def temperature_value(
    *,
    celsius: float,
    temperature_unit: TemperatureUnit | object,
) -> float:
    """Convert a Celsius value to the requested display unit."""
    if normalize_temperature_unit(temperature_unit) == TemperatureUnit.FAHRENHEIT:
        return celsius * 9.0 / 5.0 + 32.0
    return celsius


def temperature_suffix(temperature_unit: TemperatureUnit | object) -> str:
    """Return the short unit suffix without the degree sign."""
    if normalize_temperature_unit(temperature_unit) == TemperatureUnit.FAHRENHEIT:
        return "F"
    return "C"
