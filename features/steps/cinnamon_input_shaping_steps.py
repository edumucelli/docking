"""Check native GTK pointer delivery through the Cinnamon surface's input mask."""

import time
from unittest.mock import MagicMock

import gi
from behave import given, then, when

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from docking.platform.backends.base import Rect
from docking.platform.backends.cinnamon.shell import CinnamonShellSurfaceService


def settle_events():
    until = time.monotonic() + 0.12
    while time.monotonic() < until:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        time.sleep(0.005)


@given("a Cinnamon dock surface above another GTK window")
def layered_surfaces(context):
    context.shaped_events = []
    context.shaped_windows = []
    for name in ("beneath", "dock"):
        window = Gtk.Window(type=Gtk.WindowType.POPUP)
        window.set_default_size(400, 300)
        window.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        window.connect(
            "motion-notify-event",
            lambda _w, _e, n=name: context.shaped_events.append(n),
        )
        if name == "dock":
            service = CinnamonShellSurfaceService(client=MagicMock())
            service.configure_before_realize(window)
        window.move(0, 0)
        window.show_all()
        context.shaped_windows.append(window)
        context.add_cleanup(window.destroy)
    settle_events()
    service.update_input_region(Rect(150, 200, 100, 100))
    settle_events()


def move_pointer(context, x, y):
    context.shaped_events.clear()
    display = Gdk.Display.get_default()
    pointer = display.get_default_seat().get_pointer()
    pointer.warp(Gdk.Screen.get_default(), x, y)
    display.sync()
    settle_events()


@when("the pointer moves outside the active dock region")
def pointer_outside(context):
    move_pointer(context, 50, 50)


@then("the window beneath receives the pointer motion")
def pointer_beneath(context):
    assert "beneath" in context.shaped_events, context.shaped_events
    assert "dock" not in context.shaped_events, context.shaped_events


@when("the pointer moves inside the active dock region")
def pointer_inside(context):
    move_pointer(context, 200, 250)


@then("the dock surface receives the pointer motion")
def pointer_dock(context):
    assert "dock" in context.shaped_events, context.shaped_events
    assert "beneath" not in context.shaped_events, context.shaped_events
