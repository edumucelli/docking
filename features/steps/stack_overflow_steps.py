"""Real GTK allocation/scrolling; content and click dispatch are fixtures."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from behave import given, then, when

from docking.core.position import Position
from docking.ui.geometry import Rect
from docking.ui.input_controller import DockInputController
from tests.ui.preview_support import settle_gtk


@when("a dock motion event arrives outside its active band")
def transit_outside_dock(context):
    frame = SimpleNamespace(
        cursor_rect=Rect(0, 0, 100, 54), item_at_point=lambda *_: None
    )
    window = SimpleNamespace(
        _build_and_store_geometry_frame=lambda: frame,
        update_input_region=lambda **_: None,
        _schedule_redraw=lambda: None,
        dock_hovered=False,
    )
    interactions = SimpleNamespace(
        close_stack_unless_target=lambda _: context.stack.stack.close()
    )
    controller = DockInputController(
        window=window,
        interactions=interactions,
        dnd=MagicMock(),
        application_launcher=MagicMock(),
        target_service=MagicMock(),
    )
    controller._on_motion(MagicMock(), SimpleNamespace(x=50, y=80))
    settle_gtk()


@then("the stack remains visible for entry selection")
def stack_stays_visible(context):
    assert context.stack.popup.get_visible()
    context.stack.scroll_to(True)
    context.stack.click_label(-1)
    assert context.stack.activated[-1] == 8


@given('a large stack at the "{edge}" edge')
def large_stack(context, edge):
    context.stack_edge = Position(edge)
    context.stack.show(position=context.stack_edge)


@then("the stack popup fits its workarea")
def bounded_stack(context):
    context.stack.assert_bounded()


@when("I scroll and activate the last stack entry")
def last_entry(context):
    context.stack.scroll_to(True)
    context.stack.click_label(-1)


@then("the last stack entry action is called")
def last_called(context):
    assert context.stack.activated[-1] == 8


@when("I reopen the stack and activate its first entry")
def first_entry(context):
    context.stack.show(position=context.stack_edge)
    context.stack.scroll_to(False)
    context.stack.click_label(1)


@then("the first stack entry action is called")
def first_called(context):
    assert context.stack.activated[-1] == 0


@when("I reopen the stack and activate its action chip")
def action_chip(context):
    context.stack.show(position=context.stack_edge)
    context.stack.scroll_to(False)
    context.stack.click_label(0)


@then("the stack action chip callback is called")
def action_called(context):
    assert context.stack.activated[-1] == "action"


@when("I replace it with a short 48 pixel stack")
def short_stack(context):
    context.stack.show(count=2, icon_size=48)


@then("the short stack has no scrolling container")
def no_scroller(context):
    assert context.stack.scroller is None


@given('a native stack popup at the "{edge}" edge')
def native_stack(context, edge):
    context.stack_edge = Position(edge)
    context.stack.use_native_popup_contract()
    context.stack.show(position=context.stack_edge)


@when("the compositor grants a 300 by 180 viewport")
def smaller_viewport(context):
    assert context.stack.scroller.get_size_request() == (-1, -1)
    context.stack.popup.resize(300, 180)
    for _ in range(7):
        settle_gtk()
    assert tuple(context.stack.popup.get_size()) == (300, 180)


@then("the last stack entry remains reachable")
def reachable_last_entry(context):
    context.stack.scroll_to(True)
    context.stack.click_label(-1)
    assert context.stack.activated[-1] == 8
