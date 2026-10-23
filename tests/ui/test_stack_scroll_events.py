"""Muffin can deliver discrete wheel events to the popup instead of its viewport."""

import os
import subprocess
import sys

import gi
import pytest

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from docking.core.position import Position
from tests.ui.preview_support import settle_gtk
from tests.ui.stack_support import StackHarness

pytestmark = pytest.mark.skipif(
    not os.environ.get("DISPLAY"), reason="requires an X11 GTK display"
)


@pytest.mark.parametrize("target", ["popup", "content"])
def test_discrete_wheel_scrolls_the_stack_viewport(target):
    # Keep native event dispatch isolated from the suite's GTK test doubles.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from tests.ui.test_stack_scroll_events import _scroll_stack; "
                "_scroll_stack(sys.argv[1])"
            ),
            target,
        ],
        env={
            **os.environ,
            "GDK_BACKEND": "x11",
            "DISPLAY": Gdk.Display.get_default().get_name(),
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _scroll_stack(target):
    harness = StackHarness()
    try:
        harness.show(position=Position.TOP)
        adjustment = harness.scroller.get_vadjustment()
        assert adjustment.get_value() == 0
        widget = (
            harness.popup if target == "popup" else harness.stack._folder_stack_area
        )
        event = Gdk.Event.new(Gdk.EventType.SCROLL)
        event.window = widget.get_window()
        event.direction = Gdk.ScrollDirection.DOWN
        event.x, event.y = 50, 50
        event.time = Gdk.CURRENT_TIME
        event.set_device(widget.get_display().get_default_seat().get_pointer())

        Gtk.main_do_event(event)
        for _ in range(5):
            settle_gtk()

        assert adjustment.get_value() > 0
    finally:
        harness.close()
