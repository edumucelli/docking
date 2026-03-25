"""Pure conversion rules and currency-rate support for the Unit Converter applet.

This module carries the real semantic weight of the unit converter. The popup
UI is only a shell around the data and formulas defined here.

What lives here

- the category enum shown by the applet,
- the unit tables for static categories,
- the conversion algorithm for base-unit categories,
- temperature special-case math,
- currency-rate fetching and installation,
- result formatting and preference payload helpers.

Why currency is handled specially

Most categories can convert through a fixed base unit. Currency cannot, because
exchange rates change over time. This module therefore treats currency as a
runtime-populated category whose unit table is filled from a remote API. The
applet layer does the asynchronous scheduling, but the data contract and math
still belong here.

Keeping all of that in one GTK-free module matters because it lets the converter
remain testable and deterministic for the static categories while still making
its dynamic behavior explicit.
"""

from __future__ import annotations

import json
import urllib.request
from enum import Enum
from typing import Any, NamedTuple

from docking.log import get_logger

_log = get_logger(name="unitconverter.state")

_FRANKFURTER_URL = "https://api.frankfurter.dev/v2/currencies"
_FRANKFURTER_RATES_URL = "https://api.frankfurter.dev/v2/rates?base={base}"
_FETCH_TIMEOUT_S = 5


class Category(str, Enum):
    LENGTH = "Length"
    WEIGHT = "Weight"
    VOLUME = "Volume"
    TEMPERATURE = "Temperature"
    SPEED = "Speed"
    TIME = "Time"
    DATA = "Data"
    CURRENCY = "Currency"


class Unit(NamedTuple):
    name: str
    symbol: str
    factor: float  # multiply value by factor to get base unit


# Base units: m, g, ml, C, m/s, s, B

_STATIC_UNITS: dict[Category, tuple[Unit, ...]] = {
    Category.LENGTH: (
        Unit("Millimeter", "mm", 0.001),
        Unit("Centimeter", "cm", 0.01),
        Unit("Meter", "m", 1.0),
        Unit("Kilometer", "km", 1000.0),
        Unit("Inch", "in", 0.0254),
        Unit("Foot", "ft", 0.3048),
        Unit("Yard", "yd", 0.9144),
        Unit("Mile", "mi", 1609.344),
    ),
    Category.WEIGHT: (
        Unit("Milligram", "mg", 0.001),
        Unit("Gram", "g", 1.0),
        Unit("Kilogram", "kg", 1000.0),
        Unit("Pound", "lb", 453.592),
        Unit("Ounce", "oz", 28.3495),
    ),
    Category.VOLUME: (
        Unit("Milliliter", "ml", 1.0),
        Unit("Liter", "l", 1000.0),
        Unit("Fluid Ounce", "fl oz", 29.5735),
        Unit("Cup", "cup", 236.588),
        Unit("Pint", "pt", 473.176),
        Unit("Gallon", "gal", 3785.41),
    ),
    Category.TEMPERATURE: (
        Unit("Celsius", "C", 1.0),
        Unit("Fahrenheit", "F", 1.0),
        Unit("Kelvin", "K", 1.0),
    ),
    Category.SPEED: (
        Unit("Meters/second", "m/s", 1.0),
        Unit("Kilometers/hour", "km/h", 1.0 / 3.6),
        Unit("Miles/hour", "mph", 0.44704),
        Unit("Knot", "kn", 0.514444),
    ),
    Category.TIME: (
        Unit("Second", "s", 1.0),
        Unit("Minute", "min", 60.0),
        Unit("Hour", "h", 3600.0),
        Unit("Day", "day", 86400.0),
    ),
    Category.DATA: (
        Unit("Byte", "B", 1.0),
        Unit("Kilobyte", "KB", 1024.0),
        Unit("Megabyte", "MB", 1024.0**2),
        Unit("Gigabyte", "GB", 1024.0**3),
        Unit("Terabyte", "TB", 1024.0**4),
    ),
}

# Populated at runtime by fetch_currency_rates()
_currency_units: tuple[Unit, ...] = ()


def get_units(category: Category) -> tuple[Unit, ...]:
    """Return units for a category. Currency returns live-fetched units."""
    if category == Category.CURRENCY:
        return _currency_units
    return _STATIC_UNITS[category]


def get_categories() -> tuple[Category, ...]:
    """Return available categories. Currency only included if rates loaded."""
    cats = list(_STATIC_UNITS)
    if _currency_units:
        cats.append(Category.CURRENCY)
    return tuple(cats)


def currency_available() -> bool:
    return len(_currency_units) > 0


def fetch_currency_rates() -> tuple[Unit, ...] | None:
    """Fetch exchange rates from Frankfurter API (blocking).

    Returns currency units with factors relative to EUR (base),
    or None on failure. Meant to be called from a background thread.
    """
    try:
        req = urllib.request.Request(
            _FRANKFURTER_RATES_URL.format(base="EUR"),
            headers={
                "Accept": "application/json",
                "User-Agent": "Docking/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        _log.warning("Failed to fetch currency rates: %s", exc)
        return None

    # v2 response: [{"base": "EUR", "quote": "USD", "rate": 1.08}, ...]
    rates: dict[str, float] = {}
    if isinstance(data, list):
        for entry in data:
            code = entry.get("quote", "")
            rate = entry.get("rate")
            if code and isinstance(rate, (int, float)) and rate > 0:
                rates[code] = float(rate)
    elif isinstance(data, dict) and "rates" in data:
        rates = data["rates"]

    if not rates:
        _log.warning("No currency rates in response")
        return None

    # Factor = how many EUR per 1 unit of this currency (inverse of rate).
    # Generic formula: value * from_factor / to_factor
    # EUR is base, factor 1.0. For USD with rate 1.08 (1 EUR = 1.08 USD),
    # factor = 1/1.08 so that converting 100 EUR->USD = 100 * 1.0 / (1/1.08) = 108.
    units = [Unit("Euro", "EUR", 1.0)]
    for code, rate in sorted(rates.items()):
        if isinstance(rate, (int, float)) and rate > 0:
            units.append(Unit(code, code, 1.0 / float(rate)))
    return tuple(units)


def set_currency_units(units: tuple[Unit, ...]) -> None:
    """Install fetched currency units (called from main thread callback)."""
    global _currency_units
    _currency_units = units


def _to_celsius(value: float, unit: Unit) -> float:
    if unit.symbol == "F":
        return (value - 32) * 5 / 9
    if unit.symbol == "K":
        return value - 273.15
    return value


def _from_celsius(value: float, unit: Unit) -> float:
    if unit.symbol == "F":
        return value * 9 / 5 + 32
    if unit.symbol == "K":
        return value + 273.15
    return value


def convert(
    *,
    value: float,
    from_unit: Unit,
    to_unit: Unit,
    category: Category,
) -> float:
    """Convert value between units within the same category.

    For currency, factor = rate relative to EUR.
    Converting X from_unit to to_unit: X * from_factor / to_factor.
    """
    if category == Category.TEMPERATURE:
        celsius = _to_celsius(value, from_unit)
        return _from_celsius(celsius, to_unit)
    base = value * from_unit.factor
    return base / to_unit.factor


def format_result(value: float) -> str:
    """Format conversion result with smart decimal handling."""
    if abs(value) >= 100:
        return f"{value:,.2f}"
    if abs(value) >= 1:
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if abs(value) >= 0.0001:
        return f"{value:.6f}".rstrip("0").rstrip(".")
    return f"{value:.10g}"


def prefs_payload(
    *,
    category_index: int,
    from_index: int,
    to_index: int,
) -> dict[str, Any]:
    return {
        "category_index": category_index,
        "from_index": from_index,
        "to_index": to_index,
    }
