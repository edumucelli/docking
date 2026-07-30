"""Recognize and evaluate inline unit-conversion queries."""

from __future__ import annotations

import re
from dataclasses import dataclass

from docking.applets.unitconverter.state import (
    Category,
    Unit,
    convert,
    format_result,
    get_units,
)

_CONVERSION_RE = re.compile(
    r"""
    ^\s*
    (?P<value>[+-]?(?:\d+(?:,\d{3})*|\d*)(?:\.\d+)?)
    \s*
    (?P<source>[A-Za-z°/ ]+?)
    \s+(?:to|in|as|->)\s+
    (?P<target>[A-Za-z°/ ]+?)
    \s*$
    """,
    flags=re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class UnitConversion:
    """One parsed and evaluated static unit conversion."""

    expression: str
    value: float
    source: Unit
    target: Unit
    category: Category
    result: float

    @property
    def formatted_result(self) -> str:
        return format_result(self.result)


@dataclass(frozen=True, slots=True)
class CurrencyConversionRequest:
    expression: str
    value: float
    source_code: str
    target_code: str


_KNOWN_CURRENCY_CODES = {
    "AUD",
    "BRL",
    "CAD",
    "CHF",
    "CNY",
    "CZK",
    "DKK",
    "EUR",
    "GBP",
    "HKD",
    "HUF",
    "IDR",
    "ILS",
    "INR",
    "ISK",
    "JPY",
    "KRW",
    "MXN",
    "MYR",
    "NOK",
    "NZD",
    "PHP",
    "PLN",
    "RON",
    "SEK",
    "SGD",
    "THB",
    "TRY",
    "USD",
    "ZAR",
}


def _normalized_unit(value: str) -> str:
    return " ".join(
        value.casefold()
        .replace("°", "")
        .replace(".", "")
        .replace("metres", "meters")
        .replace("metre", "meter")
        .replace("litres", "liters")
        .replace("litre", "liter")
        .split()
    )


def _unit_aliases(unit: Unit) -> set[str]:
    name = _normalized_unit(unit.name)
    aliases = {name, _normalized_unit(unit.symbol)}
    if not name.endswith("s"):
        aliases.add(f"{name}s")
    return {alias for alias in aliases if alias}


def _unit_index() -> dict[str, tuple[Category, Unit]]:
    index: dict[str, tuple[Category, Unit]] = {}
    explicit_aliases = {
        "feet": "ft",
        "foot": "ft",
        "inches": "in",
        "pounds": "lb",
        "pound": "lb",
        "lbs": "lb",
        "ounces": "oz",
        "ounce": "oz",
        "kph": "km/h",
        "kilometers per hour": "km/h",
        "kilometers/hour": "km/h",
        "miles per hour": "mph",
        "meters per second": "m/s",
        "celsius": "c",
        "fahrenheit": "f",
        "kelvin": "k",
    }
    by_symbol: dict[str, tuple[Category, Unit]] = {}
    for category in Category:
        if category is Category.CURRENCY:
            continue
        for unit in get_units(category):
            record = category, unit
            by_symbol[_normalized_unit(unit.symbol)] = record
            for alias in _unit_aliases(unit):
                index.setdefault(alias, record)
    for alias, symbol in explicit_aliases.items():
        record = by_symbol.get(_normalized_unit(symbol))
        if record is not None:
            index[alias] = record
    return index


_UNITS = _unit_index()


def parse_unit_conversion(expression: str) -> UnitConversion | None:
    """Parse and evaluate ``10 km to mi`` style expressions."""
    match = _CONVERSION_RE.fullmatch(expression)
    if match is None:
        return None
    value_text = match.group("value")
    if not value_text or value_text in {"+", "-", ".", "+.", "-."}:
        return None
    source_record = _UNITS.get(_normalized_unit(match.group("source")))
    target_record = _UNITS.get(_normalized_unit(match.group("target")))
    if source_record is None or target_record is None:
        return None
    source_category, source = source_record
    target_category, target = target_record
    if source_category is not target_category:
        return None
    value = float(value_text.replace(",", ""))
    result = convert(
        value=value,
        from_unit=source,
        to_unit=target,
        category=source_category,
    )
    return UnitConversion(
        expression=expression.strip(),
        value=value,
        source=source,
        target=target,
        category=source_category,
        result=result,
    )


def parse_currency_conversion(
    expression: str,
) -> CurrencyConversionRequest | None:
    """Parse ``10 USD to EUR`` without performing network access."""
    match = _CONVERSION_RE.fullmatch(expression)
    if match is None:
        return None
    source_code = match.group("source").strip().upper()
    target_code = match.group("target").strip().upper()
    if (
        source_code not in _KNOWN_CURRENCY_CODES
        or target_code not in _KNOWN_CURRENCY_CODES
    ):
        return None
    value_text = match.group("value")
    if not value_text:
        return None
    return CurrencyConversionRequest(
        expression=expression.strip(),
        value=float(value_text.replace(",", "")),
        source_code=source_code,
        target_code=target_code,
    )


__all__ = [
    "CurrencyConversionRequest",
    "UnitConversion",
    "parse_currency_conversion",
    "parse_unit_conversion",
]
