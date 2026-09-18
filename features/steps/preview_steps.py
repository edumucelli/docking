"""Behavior steps using the real GTK preview surface."""

from behave import then, when

from docking.core.position import Position
from docking.platform.backends.base import WindowId


@when("I open {count:d} window previews at the {edge} edge")
def open_previews(context, count, edge):
    context.preview.show(count, Position(edge))


@then("the entire preview popup fits its monitor workarea")
def preview_fits(context):
    context.preview.assert_bounded()


@then("the preview contains {count:d} window cards")
def preview_card_count(context, count):
    assert len(context.preview.cards) == count


@when("I remember the preview size")
def remember_size(context):
    context.preview_size = tuple(context.preview.popup.get_size())


@then("the preview returns to its remembered size")
def preview_restores_size(context):
    assert tuple(context.preview.popup.get_size()) == context.preview_size


@then("the preview scroll position is reset")
def preview_scroll_resets(context):
    assert context.preview.scroller.get_hadjustment().get_value() == 0
    assert context.preview.scroller.get_vadjustment().get_value() == 0


@when("I scroll to the {end} preview at the {edge} edge")
def scroll_preview(context, end, edge):
    context.preview.scroll_to(last=end == "last", position=Position(edge))


@then("I can activate preview card {number:d}")
def activate_preview(context, number):
    context.preview.assert_card_visible(number - 1)
    context.preview.click_card(number - 1)
    context.preview.tracker.activate.assert_called_with(WindowId.x11(number))
    assert not context.preview.popup.get_visible()


@when("I use the mouse wheel over the preview")
def wheel_preview(context):
    context.preview.wheel_down()


@then("the preview has scrolled horizontally")
def preview_scrolled_horizontally(context):
    assert context.preview.scroller.get_hadjustment().get_value() > 0
    assert context.preview.scroller.get_vadjustment().get_value() == 0
