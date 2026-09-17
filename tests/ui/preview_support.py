"""Real GTK preview surface shared by behavior and allocation tests.

Only window discovery/capture and optional monitor bounds are simulated. Widget
construction, sizing, scrolling, painting and thumbnail activation are production.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk

from docking.core.position import Position
from docking.platform.backends.base import (
    PreviewImage,
    PreviewService,
    Rect,
    WindowId,
    WindowService,
    WindowSnapshot,
)
from docking.ui.preview import THUMB_H, THUMB_W, PreviewPopup


def settle_gtk() -> None:
    """Dispatch asynchronous frame/layout work, not just the current event queue."""
    loop = GLib.MainLoop()
    GLib.timeout_add(80, lambda: (loop.quit(), False)[1])
    loop.run()


class PreviewHarness:
    def __init__(self, bounds: Rect | None = None) -> None:
        self.bounds = bounds or Rect(0, 0, 1280, 800)
        self.tracker = MagicMock(spec=WindowService)
        self.tracker.icon_name_for_desktop.return_value = "application-x-executable"
        capture = MagicMock(spec=PreviewService)
        pixbuf = GdkPixbuf.Pixbuf.new(
            GdkPixbuf.Colorspace.RGB, False, 8, THUMB_W, THUMB_H
        )
        pixbuf.fill(0x4684DCFF)
        capture.capture.return_value = PreviewImage(pixbuf, THUMB_W, THUMB_H)
        self.popup = PreviewPopup(window_tracker=self.tracker, preview_service=capture)
        self._bounds_patch = patch(
            "docking.ui.preview.popup_workarea", side_effect=lambda *_: self.bounds
        )
        self._bounds_patch.start()

    def show(self, count: int, position: Position = Position.BOTTOM) -> None:
        self.tracker.list_preview_windows.return_value = [
            WindowSnapshot(
                id=WindowId.x11(i + 1),
                desktop_id="test.desktop",
                title=f"Window {i + 1}",
                can_activate=True,
                can_preview=True,
            )
            for i in range(count)
        ]
        area = self.bounds
        x, y = area.x + area.width / 2 - 24, area.bottom - 60
        if position == Position.TOP:
            y = area.y + 60
        elif position in (Position.LEFT, Position.RIGHT):
            x = area.x + 60 if position == Position.LEFT else area.right - 60
            y = area.y + area.height / 2 - 24
        self.popup.show_for_item("test.desktop", x, 48, y, position)
        settle_gtk()

    @property
    def scroller(self) -> Gtk.ScrolledWindow:
        widget = self.popup.get_child()
        assert isinstance(widget, Gtk.ScrolledWindow)
        return widget

    @property
    def cards(self) -> list[Gtk.Widget]:
        return self.scroller.get_child().get_child().get_children()

    def scroll_to(self, *, last: bool, position: Position) -> None:
        adjustment = (
            self.scroller.get_hadjustment()
            if position in (Position.TOP, Position.BOTTOM)
            else self.scroller.get_vadjustment()
        )
        adjustment.set_value(
            adjustment.get_upper() - adjustment.get_page_size() if last else 0
        )
        settle_gtk()

    def click_card(self, index: int) -> None:
        event = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
        event.button = 1
        self.cards[index].emit("button-press-event", event)

    def assert_card_visible(self, index: int) -> None:
        card = self.cards[index]
        viewport = self.scroller.get_child()
        x, y = card.translate_coordinates(viewport, 0, 0)
        assert x >= 0 and y >= 0
        assert x + card.get_allocated_width() <= viewport.get_allocated_width()
        assert y + card.get_allocated_height() <= viewport.get_allocated_height()

    def assert_bounded(self) -> None:
        # GDK's origin is logical even on X11 builds where Gtk.Window's popup
        # position query reports device pixels at 2x.
        _, x, y = self.popup.get_window().get_origin()
        width, height = self.popup.get_size()
        assert self.bounds.x <= x <= self.bounds.right - width
        assert self.bounds.y <= y <= self.bounds.bottom - height

    def wheel_down(self) -> None:
        event = Gdk.Event.new(Gdk.EventType.SCROLL)
        event.direction = Gdk.ScrollDirection.DOWN
        event.state = Gdk.ModifierType(0)
        self.scroller.emit("scroll-event", event)
        settle_gtk()

    def close(self) -> None:
        self.popup._cancel_hide_timer()
        self.popup.destroy()
        self._bounds_patch.stop()
        settle_gtk()
