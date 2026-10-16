"""Finite unit conversions must not lose their result in an intermediate step."""

import math
from decimal import Decimal, localcontext

import pytest

from docking.applets.unitconverter.state import Category, Unit, convert
from docking.search.services.currency_rates import CurrencyRatesCatalog


@pytest.mark.parametrize(
    "category,source,target,value,expected",
    [
        (Category.LENGTH, "km", "km", "1e308", 1e308),
        (Category.LENGTH, "mm", "mm", "5e-324", 5e-324),
        (Category.TEMPERATURE, "F", "C", "1e308", 5.555555555555556e307),
        (Category.TEMPERATURE, "C", "F", "8e307", 1.44e308),
        (Category.TEMPERATURE, "F", "F", "1e-308", 1e-308),
    ],
)
def test_converter_input_displays_a_finite_result(
    tmp_path, category, source, target, value, expected
):
    from gi.repository import Gtk

    from docking.applets.unitconverter.applet import UnitConverterApplet
    from docking.applets.unitconverter.state import get_categories, get_units
    from docking.core.config import Config

    applet = UnitConverterApplet(48, config=Config.load(tmp_path / "dock.json"))
    applet._cat_idx = get_categories().index(category)
    units = get_units(category)
    applet._from_idx = next(i for i, unit in enumerate(units) if unit.symbol == source)
    applet._to_idx = next(i for i, unit in enumerate(units) if unit.symbol == target)
    applet._entry, applet._result_label = Gtk.Entry(), Gtk.Label()
    try:
        applet._entry.set_text(value)
        applet._on_input_changed(applet._entry)
        text = applet._result_label.get_text().rsplit(" ", 1)[0]
        actual = float(text.replace(",", ""))
        assert math.isfinite(actual)
        assert math.isclose(actual, expected, rel_tol=5e-10, abs_tol=0), text
    finally:
        applet._entry.destroy()
        applet._result_label.destroy()
        applet.stop()


@pytest.mark.parametrize("value", [1e308, -1e308, 5e-324, -5e-324])
@pytest.mark.parametrize("source,target", [(1000.0, 1000.0), (0.001, 0.001)])
def test_identity_conversion_preserves_every_finite_value(value, source, target):
    assert (
        convert(
            value=value,
            from_unit=Unit("from", "a", source),
            to_unit=Unit("to", "b", target),
            category=Category.LENGTH,
        )
        == value
    )


@pytest.mark.parametrize(
    "value,source,target",
    [
        (1e308, 1000.0, 1e6),
        (5e-324, 0.001, 1e-6),
        (-1e308, 1000.0, 1e6),
        (-5e-324, 0.001, 1e-6),
    ],
)
def test_factor_conversion_keeps_a_representable_final_result(value, source, target):
    with localcontext() as context:
        context.prec = 80
        expected = float(
            Decimal.from_float(value)
            * Decimal.from_float(source)
            / Decimal.from_float(target)
        )
    actual = convert(
        value=value,
        from_unit=Unit("from", "a", source),
        to_unit=Unit("to", "b", target),
        category=Category.LENGTH,
    )
    assert math.isclose(actual, expected, rel_tol=2e-15, abs_tol=0)


@pytest.mark.parametrize("value,factor", [(1e308, 3.0), (5e-324, 0.0001)])
def test_live_currency_identity_conversion_does_not_overflow_or_underflow(
    value, factor
):
    catalog = CurrencyRatesCatalog(schedule_idle=lambda *_args: 0)
    catalog._units = (Unit("Fixture currency", "USD", factor),)
    assert catalog.convert(value=value, source_code="USD", target_code="USD") == value


@pytest.mark.parametrize(
    "value,source,target",
    [
        (1e308, "F", "C"),
        (-1e308, "F", "C"),
        (8e307, "C", "F"),
        (-8e307, "C", "F"),
    ],
)
def test_temperature_conversion_avoids_intermediate_overflow(value, source, target):
    with localcontext() as context:
        context.prec = 80
        decimal = Decimal.from_float(value)
        expected = float(
            (decimal - 32) * 5 / 9 if source == "F" else decimal * 9 / 5 + 32
        )
    actual = convert(
        value=value,
        from_unit=Unit(source, source, 1),
        to_unit=Unit(target, target, 1),
        category=Category.TEMPERATURE,
    )
    assert math.isclose(actual, expected, rel_tol=2e-15, abs_tol=0)


@pytest.mark.parametrize("symbol", ["F", "K"])
@pytest.mark.parametrize("value", [5e-324, 1e-308, -1e-308])
def test_temperature_identity_does_not_lose_a_small_value(symbol, value):
    unit = Unit(symbol, symbol, 1)
    assert (
        convert(
            value=value, from_unit=unit, to_unit=unit, category=Category.TEMPERATURE
        )
        == value
    )
