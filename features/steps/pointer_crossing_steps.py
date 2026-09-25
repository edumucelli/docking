"""Exercise raw crossing routing through the real interaction/autohide policy."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from behave import given, then, when
from gi.repository import Gdk

from docking.ui.geometry import Rect
from docking.ui.input_controller import DockInputController
from docking.ui.interaction import DockInteractionCoordinator


@given('a hovered "{backend}" dock whose pointer query reports "{location}"')
def step_hovered_crossing_dock(context, backend, location):
    assert backend in ("X11", "Wayland")
    assert location in ("inside", "outside")
    dock_surface = object()
    pointer = SimpleNamespace(
        get_position=MagicMock(
            return_value=(None, 20 if location == "inside" else 200, 20)
        ),
        get_window_at_position=MagicMock(
            return_value=(dock_surface if location == "inside" else None, 20, 20)
        ),
    )
    display = SimpleNamespace(
        get_default_seat=lambda: SimpleNamespace(get_pointer=lambda: pointer)
    )
    if backend == "X11":
        display.get_xdisplay = lambda: None
    window = SimpleNamespace(
        get_display=lambda: display,
        get_realized=lambda: True,
        get_window=lambda: dock_surface,
        get_position=lambda: (0, 0),
        current_interaction_frame=lambda: SimpleNamespace(
            cursor_rect=Rect(0, 0, 100, 100)
        ),
        autohide=context.harness.autohide,
        dock_hovered=True,
        zoom_animator=MagicMock(),
        hover=MagicMock(),
        tooltip=MagicMock(),
        preview=None,
        cursor_x=20.0,
        cursor_y=20.0,
        update_input_region=MagicMock(),
        drawing_area=MagicMock(),
    )
    window.interaction = DockInteractionCoordinator(window)
    context.crossing_window = window
    context.crossing_pointer = pointer
    context.crossing_mode = Gdk.CrossingMode.NORMAL
    window.autohide.on_mouse_enter()
    context.harness.advance_time(64)


@when('the dock receives a "{detail}" leave crossing')
def step_leave_crossing(context, detail):
    window = context.crossing_window
    event = SimpleNamespace(
        detail=getattr(Gdk.NotifyType, detail),
        mode=context.crossing_mode,
        x=-1.0 if detail == "ANCESTOR" else 20.0,
        y=20.0,
    )
    DockInputController._on_leave(
        SimpleNamespace(_window=window), window.drawing_area, event
    )


@given("a dock menu holds autohide open")
def step_menu_holds_dock(context):
    context.crossing_window.interaction.menu_popup_opened()
    context.crossing_mode = Gdk.CrossingMode.GRAB


@when("the dock menu closes with the pointer outside")
def step_menu_closes_outside(context):
    interaction = context.crossing_window.interaction
    context.crossing_pointer.get_window_at_position.return_value = (None, 20, 20)
    interaction.menu_popup_closed()


@given("a preview is visible during the crossing")
def step_crossing_preview(context):
    context.crossing_window.preview = MagicMock()
    context.crossing_window.preview.get_visible.return_value = True


@then("preview dismissal is scheduled")
def step_preview_dismissal(context):
    context.crossing_window.preview.schedule_hide.assert_called_once()


@then("no global dock pointer query was made")
def step_no_global_pointer_query(context):
    context.crossing_pointer.get_position.assert_not_called()
