"""Shared GTK region selection with Escape/right-click cancellation."""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from docking.applets.popup import (
    PopupAnchor,
    create_capture_overlay,
    dismiss_capture_overlay,
    draw_transparent_capture_overlay,
)

from .backend import ScreenshotBackend
from .state import SelectedRegion


class RegionSelection:
    def __init__(
        self,
        on_selected: Callable[[SelectedRegion], None],
        *,
        anchor: PopupAnchor | None,
        backend: ScreenshotBackend,
    ) -> None:
        self._selected = on_selected
        self._backend = backend
        self._start: tuple[float, float] | None = None
        self._end: tuple[float, float] | None = None
        self.window = create_capture_overlay(
            draw_handler=self._draw,
            click_handler=self._press,
            release_handler=self._release,
            motion_handler=self._motion,
            key_handler=self._key,
            cursor_type=Gdk.CursorType.CROSSHAIR,
            anchor=anchor,
        )
        if self.window is not None:
            try:
                backend.begin_selection(self.window)
            except OSError:
                self.close()

    def close(self) -> None:
        self._backend.end_selection()
        dismiss_capture_overlay(self.window)
        self.window = None

    def _draw(self, widget: Gtk.Window, cr) -> bool:
        draw_transparent_capture_overlay(widget, cr)
        if self._start is not None and self._end is not None:
            x, y = self._start
            right, bottom = self._end
            window = widget.get_window()
            if window is not None:
                success, origin_x, origin_y = window.get_origin()
                if success:
                    cr.set_source_rgba(0.2, 0.6, 1.0, 1.0)
                    cr.set_line_width(2)
                    cr.rectangle(
                        min(x, right) - origin_x,
                        min(y, bottom) - origin_y,
                        abs(right - x),
                        abs(bottom - y),
                    )
                    cr.stroke()
        return True

    def _press(self, _widget: Gtk.Window, event: Gdk.EventButton) -> bool:
        if event.button == 1:
            self._start = self._end = self._backend.selection_point(
                "press", float(event.x_root), float(event.y_root)
            )
            if self._start is None:
                self.close()
        else:
            self.close()
        return True

    def _motion(self, widget: Gtk.Window, event: Gdk.EventMotion) -> bool:
        if self._start is not None:
            point = self._backend.selection_point(
                "motion", float(event.x_root), float(event.y_root)
            )
            if point is not None:
                self._end = point
            widget.queue_draw()
        return True

    def _release(self, _widget: Gtk.Window, event: Gdk.EventButton) -> bool:
        start = self._start
        end = self._backend.selection_point(
            "release", float(event.x_root), float(event.y_root)
        )
        self.close()
        if event.button == 1 and start is not None and end is not None:
            x, y = start
            end_x, end_y = end
            if x != end_x and y != end_y:
                self._selected(
                    SelectedRegion(
                        min(x, end_x), min(y, end_y), abs(end_x - x), abs(end_y - y)
                    )
                )
        return True

    def _key(self, _widget: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.close()
        return True
