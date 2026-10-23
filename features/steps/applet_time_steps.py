"""Verify rendered applet pixels after controlled changes to local time."""

import time
from unittest.mock import patch

from behave import given, then, when

import docking.applets.calendar.applet as calendar_mod
import docking.applets.clock.applet as clock_mod
from docking.applets.calendar.applet import CalendarApplet
from docking.applets.calendar.render import render_icon as calendar_icon
from docking.applets.calendar.state import CalendarSnapshot
from docking.applets.clock.applet import ClockApplet
from docking.applets.clock.render import render_icon as clock_icon
from docking.core.config import Config


@given("a calendar showing Thursday January 15")
def calendar_initial(context):
    context.calendar_snapshot = CalendarSnapshot(15, "Thu", "Thu, Jan 15")
    replacement = patch.object(
        calendar_mod, "snapshot_from", lambda: context.calendar_snapshot
    )
    replacement.start()
    context.add_cleanup(replacement.stop)
    context.calendar_applet = CalendarApplet(48, config=Config())
    context.old_calendar_pixels = bytes(context.calendar_applet.item.icon.get_pixels())


@when("local time changes to Sunday February 15")
def calendar_later(context):
    context.calendar_snapshot = CalendarSnapshot(15, "Sun", "Sun, Feb 15")
    context.calendar_applet._tick()


@then("the calendar icon shows the new weekday and its tooltip shows February")
def calendar_updated(context):
    expected = calendar_icon(size=48, snapshot=context.calendar_snapshot)
    actual = bytes(context.calendar_applet.item.icon.get_pixels())
    assert actual != context.old_calendar_pixels
    assert actual == bytes(expected.get_pixels())
    assert context.calendar_applet.item.name == "Sun, Feb 15"


@given("a digital clock showing 2026-01-15 10:30 with its date")
def clock_initial(context):
    context.clock_time = time.strptime("2026-01-15 10:30", "%Y-%m-%d %H:%M")
    replacement = patch.object(
        clock_mod.time, "localtime", lambda *_args: context.clock_time
    )
    replacement.start()
    context.add_cleanup(replacement.stop)
    config = Config(applet_prefs={"clock": {"show_digital": True, "show_date": True}})
    context.clock_applet = ClockApplet(48, config=config)
    context.old_clock_pixels = bytes(context.clock_applet.item.icon.get_pixels())


@when("the clock's local time changes to {later}")
def clock_later(context, later):
    context.clock_time = time.strptime(later, "%Y-%m-%d %H:%M")
    context.clock_applet._on_tick()


@then("the clock icon and tooltip show the new local time")
def clock_updated(context):
    expected = clock_icon(
        size=48,
        now=context.clock_time,
        show_digital=True,
        show_date=True,
        show_military=False,
        show_seconds=False,
    )
    actual = bytes(context.clock_applet.item.icon.get_pixels())
    assert actual != context.old_clock_pixels
    assert actual == bytes(expected.get_pixels())
    assert time.strftime("%b %-d", context.clock_time) in context.clock_applet.item.name
    assert time.strftime("%-I:%M", context.clock_time) in context.clock_applet.item.name
