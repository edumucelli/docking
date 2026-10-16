"""Exercise config recovery through real GTK timers and the autohide controller."""

import json
import tempfile
import time
from pathlib import Path

from behave import given, then, when
from gi.repository import GLib, Gtk

from docking.core.config import Config
from docking.ui.autohide import AutoHideController, HideState


@given('a dock configuration with an oversized "{field}" value')
def saved_timing(context, field):
    directory = tempfile.TemporaryDirectory(prefix="docking-timing-bdd-")
    context.add_cleanup(directory.cleanup)
    path = Path(directory.name) / "config.json"
    path.write_text(json.dumps({"hide_mode": "autohide", field: 10**400}))
    window = Gtk.Window()
    window.show_all()
    window.queue_redraw = window.queue_draw
    context.add_cleanup(window.destroy)
    context.timing_controller = AutoHideController(window, Config.load(path))
    context.add_cleanup(context.timing_controller.reset)


def wait_for_state(controller, state):
    deadline = time.monotonic() + 2
    while controller.state is not state and time.monotonic() < deadline:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.005)
    assert controller.state is state


@when("the pointer leaves and returns using the real GTK scheduler")
def leave_and_return(context):
    controller = context.timing_controller
    controller.on_mouse_enter()
    controller.on_mouse_leave()
    wait_for_state(controller, HideState.HIDDEN)
    assert controller.hide_offset == 1
    controller.on_mouse_enter()
    wait_for_state(controller, HideState.VISIBLE)


@then("the dock completes both its hide and reveal animations")
def completed_animations(context):
    controller = context.timing_controller
    assert controller.state is HideState.VISIBLE
    assert controller.hide_offset == 0
