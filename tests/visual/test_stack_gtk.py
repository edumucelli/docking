"""F10 regression tests against asynchronous GTK allocation and real drawing."""

import cairo
import pytest
from gi.repository import Gdk, Gtk

from docking.core.position import Position
from docking.platform.backends.base import Rect
from tests.ui.preview_support import settle_gtk
from tests.ui.stack_support import StackHarness
from tests.visual.support import surface_image

pytestmark = pytest.mark.visual


@pytest.fixture
def stack():
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display required")
    harness = StackHarness()
    yield harness
    harness.close()


@pytest.mark.parametrize("position", list(Position))
def test_large_stack_is_bounded_and_scrolls_without_dropping_cards(stack, position):
    stack.show(position=position)
    stack.assert_bounded()
    assert len(stack.stack._folder_stack_cards) == 10
    assert stack.scroller is not None
    assert (
        stack.scroller.get_vadjustment().get_upper()
        > stack.scroller.get_vadjustment().get_page_size()
    )


@pytest.mark.parametrize("position", list(Position))
def test_scroll_reaches_last_first_and_action_with_correct_clicks(stack, position):
    for index, last, expected in ((-1, True, 8), (1, False, 0), (0, False, "action")):
        stack.show(position=position)
        stack.scroll_to(last)
        stack.click_label(index)
        assert stack.activated[-1] == expected
        assert not stack.popup.get_visible()


@pytest.mark.parametrize("position", list(Position))
def test_small_fan_is_unchanged_after_overflow_reuse(stack, position):
    stack.show(count=2, position=position, icon_size=48)
    original = tuple(stack.popup.get_size())
    assert stack.scroller is None
    stack.show(position=position)
    stack.scroll_to(True)
    stack.show(count=2, position=position, icon_size=48)
    assert tuple(stack.popup.get_size()) == original
    assert stack.scroller is None
    stack.assert_bounded()


@pytest.mark.parametrize("icon_size", [32, 48, 96, 128])
@pytest.mark.parametrize("position", list(Position))
def test_supported_sizes_fit_offset_panel_workarea(stack, position, icon_size):
    stack.bounds = Rect(100, 112, 800, 500)
    stack.show(position=position, icon_size=icon_size)
    stack.assert_bounded()
    assert len(stack.stack._folder_stack_cards) == 10


def test_small_workarea_allows_both_scroll_axes(stack):
    stack.bounds = Rect(200, 80, 300, 240)
    stack.show(position=Position.LEFT)
    stack.assert_bounded()
    assert stack.scroller.get_hscrollbar().get_child_visible()
    assert stack.scroller.get_vscrollbar().get_child_visible()


def test_scrolling_cancels_a_pending_click(stack):
    stack.show()
    area = stack.stack._folder_stack_area
    card = stack.stack._folder_stack_cards[1]
    event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
    event.button, event.x, event.y = 1, card.label_x + 10, card.label_y + 10
    area.emit("button-press-event", event)
    assert stack.stack._folder_stack_pressed_target == "0"
    stack.scroll_to(True)
    card = stack.stack._folder_stack_cards[-1]
    release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
    release.button, release.x, release.y = 1, card.label_x + 10, card.label_y + 10
    area.emit("button-release-event", release)
    assert stack.activated == []


@pytest.mark.parametrize("position", list(Position))
def test_scrolling_preserves_transparent_fan_background(stack, position):
    stack.show(position=position)
    for last in (False, True):
        stack.scroll_to(last)
        width, height = stack.popup.get_size()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        stack.popup.draw(cairo.Context(surface))
        pixels = surface_image(surface)
        assert pixels.getpixel((2, height // 2))[3] == 0
        assert pixels.getpixel((0, 0))[3] == 0
        assert pixels.getchannel("A").getextrema()[1] == 255


def test_refresh_rebuilds_smaller_content_and_resets_scroll(stack):
    from dataclasses import replace

    stack.show()
    stack.scroll_to(True)
    stack.content = replace(stack.content, entries=stack.content.entries[:2])
    assert stack.stack.refresh()
    for _ in range(7):
        settle_gtk()
    stack.assert_bounded()
    assert stack.scroller is None
    assert len(stack.stack._folder_stack_cards) == 3


@pytest.mark.parametrize("position", list(Position))
def test_applet_adapter_keeps_overflow_entries_reachable(stack, position):
    from unittest.mock import MagicMock

    from docking.ui.folder.stack import FolderStackController

    stack.stack = FolderStackController(
        config=stack.config,
        runtime=stack.runtime,
        dock_window=stack.parent,
        target_service=MagicMock(),
    )
    stack.show(position=position)
    x, y = stack.stack._folder_stack_anchor_x, stack.stack._folder_stack_anchor_y
    stack.stack.close()
    assert stack.stack.show_applet_stack(
        owner_id="applet://devices",
        provider=lambda _: stack.content,
        anchor_x=x,
        anchor_y=y,
        icon_w=0,
        position=position,
        parent=stack.parent,
    )
    for _ in range(7):
        settle_gtk()
    stack.assert_bounded()
    stack.scroll_to(True)
    stack.click_label(-1)
    assert stack.activated == [8]
