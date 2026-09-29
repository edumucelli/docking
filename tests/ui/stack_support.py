"""Real GTK stack surfaces; only content/actions and monitor bounds are fixtures."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gi.repository import Gdk, GdkPixbuf, Gtk

from docking.applets.popup import PopupAnchor
from docking.core.config import Config
from docking.core.position import Position
from docking.platform.backends.base import Rect
from docking.ui.stack import StackAction, StackContent, StackEntry, StackPopupController
from tests.ui.preview_support import settle_gtk


class StackHarness:
    def __init__(self, bounds: Rect | None = None):
        self.bounds = bounds or Rect(0, 0, 1280, 800)
        self.config = Config(icon_size=96)
        self.parent = Gtk.Window()
        self.runtime = MagicMock()
        self.stack = StackPopupController(
            config=self.config, runtime=self.runtime, dock_window=self.parent
        )
        self.activated = []
        self.icon = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 96, 96)
        self.icon.fill(0x4684DCFF)
        self.content = StackContent()
        self._bounds = patch(
            "docking.ui.stack.popup_workarea",
            side_effect=lambda *_: self.bounds,
            create=True,
        )
        self._bounds.start()

    def show(self, count=9, position=Position.BOTTOM, icon_size=96):
        self.config.icon_size = icon_size
        self.config.position = position.value
        self.content = StackContent(
            entries=tuple(
                StackEntry(
                    str(i), f"Item {i}", self.icon, lambda i=i: self.activated.append(i)
                )
                for i in range(count)
            ),
            action=StackAction(
                "action",
                "Open in file manager",
                lambda: self.activated.append("action"),
            ),
        )
        bounds = self.bounds
        anchors = {
            Position.BOTTOM: (bounds.x + bounds.width // 2, bounds.bottom - 104),
            Position.TOP: (bounds.x + bounds.width // 2, bounds.y + 104),
            Position.LEFT: (bounds.x + 104, bounds.y + bounds.height // 2),
            Position.RIGHT: (bounds.right - 104, bounds.y + bounds.height // 2),
        }
        x, y = anchors[position]
        self.stack.close()
        self.stack.show_stack(
            owner_id="test-stack",
            provider=lambda _: self.content,
            anchor=PopupAnchor(x=x, y=y, position=position),
        )
        for _ in range(7):
            settle_gtk()

    @property
    def popup(self):
        return self.stack._folder_stack_window

    @property
    def scroller(self):
        widget = self.stack._folder_stack_revealer.get_child()
        return widget if isinstance(widget, Gtk.ScrolledWindow) else None

    def assert_bounded(self):
        _, x, y = self.popup.get_window().get_origin()
        width, height = self.popup.get_size()
        assert self.bounds.x <= x <= self.bounds.right - width
        assert self.bounds.y <= y <= self.bounds.bottom - height

    def scroll_to(self, last):
        scroller = self.scroller
        assert scroller is not None
        adjustment = scroller.get_vadjustment()
        adjustment.set_value(
            adjustment.get_upper() - adjustment.get_page_size() if last else 0
        )
        settle_gtk()

    def click_label(self, index):
        card = self.stack._folder_stack_cards[index]
        area = self.stack._folder_stack_area
        x, y = card.label_x + card.label_w / 2, card.label_y + card.label_h / 2
        px, py = area.translate_coordinates(self.popup, int(x), int(y))
        width, height = self.popup.get_size()
        assert 0 <= px < width and 0 <= py < height
        for kind, name in (
            (Gdk.EventType.BUTTON_PRESS, "button-press-event"),
            (Gdk.EventType.BUTTON_RELEASE, "button-release-event"),
        ):
            event = Gdk.Event.new(kind)
            event.button, event.x, event.y = 1, x, y
            area.emit(name, event)

    def close(self):
        self.stack.close()
        if self.popup is not None:
            self.popup.destroy()
        self.parent.destroy()
        self._bounds.stop()
        settle_gtk()
