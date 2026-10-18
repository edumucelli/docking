"""Behavior steps using the real GTK preview surface."""

from behave import given, then, when
from gi.repository import GdkPixbuf

from docking.platform.backends.base import PreviewImage
from tests.ui.preview_support import settle_gtk


@given("a visible preview waiting for an asynchronous window capture")
def pending_preview(context):
    service = context.preview.popup._preview_service
    service.asynchronous = True
    service.capture.return_value = None
    context.preview.show(1)
    context.async_preview_image = context.preview.cards[0].get_child().get_children()[0]
    assert context.async_preview_image.get_pixbuf() is None


@when("the asynchronous window capture returns red pixels")
def ready_preview(context):
    image = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 20, 10)
    image.fill(0xFF0000FF)
    context.preview.popup._preview_service.capture.return_value = PreviewImage(
        image, 20, 10
    )
    for _ in range(5):
        settle_gtk()


@then("the visible preview image shows the red window")
def visible_preview_pixels(context):
    pixbuf = context.async_preview_image.get_pixbuf()
    assert pixbuf is not None
    assert pixbuf.get_pixels()[:4] == b"\xff\x00\x00\xff"


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
