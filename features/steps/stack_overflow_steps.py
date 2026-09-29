"""Real GTK allocation/scrolling; content and click dispatch are fixtures."""

from behave import given, then, when

from docking.core.position import Position


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
