"""Preview regressions verified against actual asynchronous GTK allocations."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from gi.repository import Gdk, Gtk

from docking.core.items import DockItem
from docking.core.position import Position
from docking.platform.backends.base import Rect, WindowId
from docking.ui.hover import HoverManager
from docking.ui.preview import POPUP_PADDING
from tests.ui.preview_support import PreviewHarness, settle_gtk

pytestmark = pytest.mark.visual


@pytest.fixture
def preview():
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display required; run under xvfb-run")
    harness = PreviewHarness()
    yield harness
    harness.close()


@pytest.fixture
def preview_hover(preview):
    """Real hover/popup timers with only dock geometry and services simulated."""
    item = DockItem(
        desktop_id="test.desktop", name="Test", is_running=True, instance_count=8
    )
    window = MagicMock()
    window.dock_hovered = True
    window.cursor_x, window.cursor_y = 600, 740
    window.get_realized.return_value = True
    window.get_position.return_value = (0, 0)
    window.autohide.enabled = False
    frame = SimpleNamespace(
        hover_item_at_point=lambda *_: item,
        geometry_for_item=lambda _: SimpleNamespace(
            draw_rect=SimpleNamespace(x=600, y=740, w=48, h=48)
        ),
    )
    config = SimpleNamespace(previews_enabled=True, pos=Position.BOTTOM)
    hover = HoverManager(
        window=window,
        config=config,
        model=MagicMock(),
        theme=MagicMock(),
        tooltip=MagicMock(),
        geometry_builder=SimpleNamespace(build_frame=lambda: frame),
    )
    hover.set_preview(preview.popup)
    hover.hovered_item = item  # Retained during dock-to-preview handoff.
    yield hover, config
    hover.cancel()


@pytest.mark.parametrize("position", list(Position))
def test_same_icon_reentry_after_activation_reopens_real_popup(
    preview, preview_hover, position
):
    hover, config = preview_hover
    config.pos = position
    preview.show(8, position)
    preview.popup.schedule_hide()
    preview.click_card(0)
    assert not preview.popup.get_visible()
    assert preview.popup.current_desktop_id == ""
    assert preview.popup._hide_timer_id == 0

    hover.update(600)
    assert hover._preview_timer_id != 0
    for _ in range(7):
        settle_gtk()

    assert preview.popup.get_visible()
    assert preview.popup.current_desktop_id == "test.desktop"
    assert len(preview.cards) == 8
    preview.assert_bounded()


@pytest.mark.parametrize("position", list(Position))
def test_same_icon_handback_keeps_real_popup_and_scroll_position(
    preview, preview_hover, position
):
    hover, _config = preview_hover
    preview.show(8, position)
    preview.scroll_to(last=True, position=position)
    scroller = preview.scroller
    adjustment = (
        scroller.get_hadjustment()
        if position in (Position.TOP, Position.BOTTOM)
        else scroller.get_vadjustment()
    )
    previous = adjustment.get_value()
    assert previous > 0
    preview.popup.schedule_hide()

    hover.update(600)
    assert hover._preview_timer_id == 0
    assert preview.popup._hide_timer_id == 0
    for _ in range(7):
        settle_gtk()

    assert preview.popup.get_visible()
    assert preview.scroller is scroller
    assert adjustment.get_value() == previous


@pytest.mark.parametrize("position", list(Position))
@pytest.mark.parametrize("count", [1, 2, 8])
def test_actual_popup_fits_and_only_overflowing_axis_scrolls(preview, position, count):
    # Given real thumbnails on each dock edge, when GTK finishes allocating.
    preview.show(count, position)

    # Then the complete surface fits and small groups retain natural size.
    preview.assert_bounded()
    assert len(preview.cards) == count
    scroller = preview.scroller
    horizontal = position in (Position.TOP, Position.BOTTOM)
    assert scroller.get_hscrollbar().get_child_visible() == (count == 8 and horizontal)
    assert scroller.get_vscrollbar().get_child_visible() == (
        count == 8 and not horizontal
    )
    if count == 1:
        card = preview.cards[0].get_preferred_size()[1]
        assert tuple(preview.popup.get_size()) == (
            card.width + 2 * POPUP_PADDING,
            card.height + 2 * POPUP_PADDING,
        )


@pytest.mark.parametrize("position", list(Position))
def test_reused_popup_grows_shrinks_and_resets_scroll(preview, position):
    preview.show(1, position)
    original = tuple(preview.popup.get_size())
    for count in (8, 3, 1, 2, 1):
        preview.show(count, position)
        preview.assert_bounded()
        assert preview.scroller.get_hadjustment().get_value() == 0
        assert preview.scroller.get_vadjustment().get_value() == 0
        if count == 8:
            preview.scroll_to(last=True, position=position)
        if count == 1:
            assert tuple(preview.popup.get_size()) == original


@pytest.mark.parametrize("position", list(Position))
def test_scroll_to_first_and_last_cards_then_activate(preview, position):
    for last in (True, False):
        preview.show(8, position)
        preview.scroll_to(last=last, position=position)
        preview.assert_card_visible(-1 if last else 0)
        preview.click_card(-1 if last else 0)
        preview.tracker.activate.assert_called_with(WindowId.x11(8 if last else 1))
        assert not preview.popup.get_visible()


@pytest.mark.parametrize("position", list(Position))
def test_small_offset_workarea_can_scroll_both_axes(preview, position):
    preview.bounds = Rect(200, 80, 180, 140)
    preview.show(8, position)
    preview.assert_bounded()
    assert preview.scroller.get_hscrollbar().get_child_visible()
    assert preview.scroller.get_vscrollbar().get_child_visible()


def test_scrollbar_interaction_does_not_release_autohide(preview):
    controller = MagicMock()
    preview.popup.set_autohide(controller)
    preview.show(8)
    event = Gdk.Event.new(Gdk.EventType.LEAVE_NOTIFY)
    event.detail = Gdk.NotifyType.INFERIOR
    event.mode = Gdk.CrossingMode.NORMAL
    preview.popup.emit("leave-notify-event", event)
    preview.scroll_to(last=True, position=Position.BOTTOM)
    settle_gtk()
    assert preview.popup._hide_timer_id == 0
    controller.on_mouse_leave.assert_not_called()


def test_preview_keeps_transparent_background_around_cards(preview):
    import cairo

    from tests.visual.support import surface_image

    preview.show(1)
    width, height = preview.popup.get_size()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    preview.popup.draw(cairo.Context(surface))
    pixels = surface_image(surface)
    # Preserve the original floating thumbnails, not a solid panel behind them.
    assert pixels.getpixel((4, height // 2))[3] == 0
    assert pixels.getpixel((0, 0))[3] == 0
    assert pixels.getpixel((width // 2, height // 2))[3] == 255


@pytest.mark.parametrize(
    "direction", [Gdk.ScrollDirection.DOWN, Gdk.ScrollDirection.SMOOTH]
)
def test_vertical_wheel_scrolls_horizontal_preview_row(preview, direction):
    preview.show(8, Position.BOTTOM)
    event = Gdk.Event.new(Gdk.EventType.SCROLL)
    event.direction = direction
    event.state = Gdk.ModifierType(0)
    event.delta_x = 0
    event.delta_y = 0.5
    assert preview.scroller.emit("scroll-event", event)
    assert preview.scroller.get_hadjustment().get_value() > 0
    assert preview.scroller.get_vadjustment().get_value() == 0


@pytest.mark.parametrize(
    "shift,dx,dy", [(True, 0, 1), (False, 1, 0), (False, 0.5, 1), (False, 0, 0)]
)
def test_native_horizontal_and_shift_gestures_are_not_translated(
    preview, shift, dx, dy
):
    preview.show(8)
    event = Gdk.Event.new(Gdk.EventType.SCROLL)
    event.direction = Gdk.ScrollDirection.SMOOTH
    event.state = Gdk.ModifierType.SHIFT_MASK if shift else Gdk.ModifierType(0)
    event.delta_x, event.delta_y = dx, dy
    assert not preview.popup._on_horizontal_scroll(preview.scroller, event)


def test_two_axis_overflow_keeps_native_vertical_wheel(preview):
    preview.bounds = Rect(0, 0, 180, 140)
    preview.show(8)
    event = Gdk.Event.new(Gdk.EventType.SCROLL)
    event.direction = Gdk.ScrollDirection.DOWN
    event.state = Gdk.ModifierType(0)
    assert not preview.popup._on_horizontal_scroll(preview.scroller, event)
