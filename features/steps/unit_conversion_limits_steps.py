"""Drive the actual converter input handler and read its GTK result label."""

import math
from pathlib import Path
from tempfile import TemporaryDirectory

import gi
from behave import given, then, when

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from docking.applets.unitconverter.applet import UnitConverterApplet
from docking.applets.unitconverter.state import Category, get_categories, get_units
from docking.core.config import Config


@given('Unit Converter is set to "{category}" from "{source}" to "{target}"')
def selected_conversion(context, category, source, target):
    directory = TemporaryDirectory(prefix="docking-bdd-converter-")
    context.add_cleanup(directory.cleanup)
    applet = UnitConverterApplet(
        48, config=Config.load(Path(directory.name) / "dock.json")
    )
    context.add_cleanup(applet.stop)
    selected = Category(category)
    applet._cat_idx = get_categories().index(selected)
    units = get_units(selected)
    applet._from_idx = next(i for i, unit in enumerate(units) if unit.symbol == source)
    applet._to_idx = next(i for i, unit in enumerate(units) if unit.symbol == target)
    applet._entry, applet._result_label = Gtk.Entry(), Gtk.Label()
    context.add_cleanup(applet._entry.destroy)
    context.add_cleanup(applet._result_label.destroy)
    context.limit_converter = applet


@when('I enter the conversion value "{value}"')
def enter_conversion(context, value):
    context.limit_converter._entry.set_text(value)
    context.limit_converter._on_input_changed(context.limit_converter._entry)


@then('the displayed conversion is finite and equals "{expected}"')
def displayed_conversion(context, expected):
    text = context.limit_converter._result_label.get_text().rsplit(" ", 1)[0]
    actual = float(text.replace(",", ""))
    assert math.isfinite(actual)
    assert math.isclose(actual, float(expected), rel_tol=5e-10, abs_tol=0), text
