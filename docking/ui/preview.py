# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

"""Window preview popup for running applications hovered in the dock.

What a preview is supposed to do

The preview popup is not just a tooltip with thumbnails. It is the continuation
of the user's interaction with a running application icon.

Typical user flow:

    pointer hovers running app
       |
       +--> preview delay expires
       |
       +--> popup with window thumbnails appears
       |
       +--> user moves from dock to preview
       |
       +--> user activates a window from the preview

That flow has one major consequence:

    dock + preview behave like one temporary interaction region

If the dock hid the moment the pointer left the icon, the preview would become
unusable. This is why preview behavior is intentionally different from tooltip
behavior.

What this module owns

This module owns:

- preview popup creation and widget structure,
- delayed hide of the preview popup,
- preview enter/leave tracking,
- window activation from thumbnail clicks,
- releasing autohide only when the preview truly stops being relevant.

It does not own:

- hover detection,
- the decision to arm preview show,
- dock-wide effective hover state,
- autohide animation math.
- platform-specific preview capture.

Those belong to HoverManager and the interaction/autohide layers.

Why preview leave is not normal leave

The important policy is:

    preview visible => leaving the dock does not immediately mean hide

ASCII view:

    +-----------+        gap        +----------------------+
    |   dock    |  ------------->   | preview popup        |
    |   icon    |                   | [thumb] [thumb] ...  |
    +-----------+                   +----------------------+

The gap exists because the preview is a separate popup window. The pointer must
physically cross that space. If the dock hid instantly on dock leave, the user
would see a hide/show flicker or lose the target before reaching it.

So the practical policy is:

- leave dock while preview visible -> schedule preview hide, do not autohide yet
- enter preview -> keep interaction alive
- leave preview and do not return -> preview hides, then autohide may proceed

That policy is one of the key behavioral differences between preview and
tooltip.

Thumbnail capture model

Preview thumbnails try to show real window contents, but the way that happens
is platform-owned. The popup asks a PreviewService for an image using a
backend-neutral WindowId. On X11 that service can still do foreign-window
capture internally; on a future native Wayland backend it should return an
image only when compositor support exists. If capture is unavailable, this UI
falls back to the app icon resolved through WindowService.

Why CSS and widget structure live here

The preview popup is a fairly self-contained UI surface:

- popup window
- thumbnail widgets
- labels
- hover styling
- click behavior

Unlike the dock itself, it does not need to participate in the full draw/input
shape model. So it is reasonable for this module to own its CSS and widget tree
instead of pushing those concerns into the main renderer.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.core.position import Position, is_horizontal
from docking.log import get_logger
from docking.platform.backends.base import WindowId, WindowService, WindowSnapshot
from docking.ui.display import clamp_to_screen

if TYPE_CHECKING:
    from docking.platform.backends.base import PreviewService
    from docking.ui.autohide import AutoHideController

log = get_logger(name="preview")

THUMB_W = 200
THUMB_H = 150
POPUP_PADDING = 8
THUMB_SPACING = 8
LABEL_MAX_CHARS = 25
PREVIEW_HIDE_DELAY_MS = 300
ICON_FALLBACK_SIZE = 64
PREVIEW_GAP_PX = 40

_CSS = b"""
.preview-popup {
    background-color: rgba(30, 30, 30, 0.92);
    border-radius: 8px;
    border: 1px solid rgba(100, 100, 100, 0.6);
    padding: 8px;
}
.preview-thumb {
    border-radius: 4px;
    border: 2px solid transparent;
    padding: 2px;
}
.preview-thumb:hover {
    border-color: rgba(100, 180, 255, 0.8);
    background-color: rgba(100, 180, 255, 0.15);
}
.preview-label {
    color: white;
    font-size: 11px;
    background-color: rgba(40, 40, 40, 0.55);
    border-radius: 3px;
    padding: 1px 4px;
    margin-top: -4px;
}
"""


def _install_css() -> None:
    """Install CSS for preview popup (once)."""
    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


@lru_cache(maxsize=1)
def _ensure_css() -> None:
    _install_css()


class PreviewPopup(Gtk.Window):
    """Floating popup showing window thumbnails for a dock item."""

    def __init__(
        self, window_tracker: WindowService, preview_service: PreviewService
    ) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        _ensure_css()

        self._tracker = window_tracker
        self._preview_service = preview_service
        self._autohide: AutoHideController | None = None
        self._pointer_inside_dock: Callable[[], bool] | None = None
        self._hide_timer_id: int = 0
        self._current_desktop_id: str = ""

        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.TOOLTIP)
        self.set_app_paintable(True)

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.get_style_context().add_class("preview-popup")

        self.connect("enter-notify-event", self._on_enter)
        self.connect("leave-notify-event", self._on_leave)

    def set_autohide(self, controller: AutoHideController | None) -> None:
        self._autohide = controller

    def set_pointer_inside_dock_probe(self, probe: Callable[[], bool]) -> None:
        self._pointer_inside_dock = probe

    def show_for_item(
        self,
        desktop_id: str,
        anchor_x: float,
        icon_w: float,
        anchor_y: float,
        position: Position = Position.BOTTOM,
    ) -> None:
        """Show preview popup near a dock icon.

        Anchor coordinates are in absolute screen-space (not window-local):
        - Horizontal docks: anchor_x = icon left edge, anchor_y = icon
          inner edge (top for bottom dock, bottom for top dock).
        - Vertical docks: anchor_x = icon inner edge, anchor_y = icon
          top edge along main axis.

        The popup is centered on the icon along the main axis and offset
        away from the screen edge along the cross axis.
        """
        windows = list(self._tracker.list_preview_windows(desktop_id))
        if not windows:
            self.hide()
            return
        icon_name = self._tracker.icon_name_for_desktop(desktop_id)

        self._current_desktop_id = desktop_id
        self._cancel_hide_timer()

        child = self.get_child()
        if child:
            self.remove(child)

        # Horizontal layout for horizontal docks, vertical for vertical
        horizontal = is_horizontal(pos=position)
        orientation = (
            Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL
        )
        box = Gtk.Box(orientation=orientation, spacing=THUMB_SPACING)
        box.set_margin_start(POPUP_PADDING)
        box.set_margin_end(POPUP_PADDING)
        box.set_margin_top(POPUP_PADDING)
        box.set_margin_bottom(POPUP_PADDING)

        for window in windows:
            thumb_widget = self._make_thumbnail_for_window(
                window=window, fallback_icon_name=icon_name
            )
            box.pack_start(thumb_widget, False, False, 0)

        self.add(box)

        box.show_all()
        preferred = box.get_preferred_size()[1]
        popup_width = max(preferred.width + 2 * POPUP_PADDING, 1)
        popup_height = max(preferred.height + 2 * POPUP_PADDING, 1)

        if position == Position.BOTTOM:
            popup_x = int(anchor_x + icon_w / 2 - popup_width / 2)
            popup_y = int(anchor_y - popup_height - PREVIEW_GAP_PX)
        elif position == Position.TOP:
            popup_x = int(anchor_x + icon_w / 2 - popup_width / 2)
            popup_y = int(anchor_y + PREVIEW_GAP_PX)
        elif position == Position.LEFT:
            popup_x = int(anchor_x + PREVIEW_GAP_PX)
            popup_y = int(anchor_y + icon_w / 2 - popup_height / 2)
        else:  # RIGHT
            popup_x = int(anchor_x - popup_width - PREVIEW_GAP_PX)
            popup_y = int(anchor_y + icon_w / 2 - popup_height / 2)

        # Clamp to screen
        screen = self.get_screen()
        screen_w = screen.get_width()
        screen_h = screen.get_height()
        popup_pos = clamp_to_screen(
            popup_x, popup_y, popup_width, popup_height, screen_w, screen_h
        )

        self.move(popup_pos.x, popup_pos.y)
        self.show_all()

    def _make_thumbnail_for_window(
        self, window: WindowSnapshot, fallback_icon_name: str
    ) -> Gtk.Widget:
        """Create a clickable thumbnail widget for a window snapshot."""
        event_box = Gtk.EventBox()
        event_box.get_style_context().add_class("preview-thumb")
        event_box.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
        )
        event_box.connect("button-press-event", self._on_thumb_click, window.id)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Thumbnail image
        preview = self._preview_service.capture(
            window.id, width=THUMB_W, height=THUMB_H
        )
        if preview is not None:
            image = Gtk.Image.new_from_pixbuf(preview.image)
        else:
            image = Gtk.Image.new_from_icon_name(
                fallback_icon_name, Gtk.IconSize.DIALOG
            )
        image.set_size_request(THUMB_W, THUMB_H)
        vbox.pack_start(image, False, False, 0)

        # Window title
        title = window.title
        if len(title) > LABEL_MAX_CHARS:
            title = title[: LABEL_MAX_CHARS - 1] + "\u2026"
        label = Gtk.Label(label=title)
        label.get_style_context().add_class("preview-label")
        label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        # In a vertical Gtk.Box, children can still receive full cross-axis
        # width (thumbnail width). Keep label at natural width so the CSS
        # background chip wraps the actual title text.
        label.set_halign(Gtk.Align.CENTER)
        label.set_hexpand(False)
        # Title is already manually truncated above, so avoid forcing a wide
        # allocation here; letting GTK use natural text width keeps the dark
        # background chip sized to the title instead of thumbnail width.
        vbox.pack_start(label, False, False, 0)

        event_box.add(vbox)
        return event_box

    def _on_thumb_click(
        self, _widget: Gtk.EventBox, _event: Gdk.EventButton, window_id: WindowId
    ) -> bool:
        """Activate the clicked window."""
        self._tracker.activate(window_id)
        self.hide()
        self._release_dock_autohide_if_needed()
        return True

    def _on_enter(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        """Keep popup and dock visible while mouse is inside preview."""
        log.debug(f"preview enter: detail={event.detail} mode={event.mode}")
        self._cancel_hide_timer()
        if self._autohide:
            self._autohide.on_mouse_enter()
        return False

    def _on_leave(self, _widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        """Start hide timer when mouse leaves popup."""
        # GTK crossing event detail types:
        #
        # GTK uses a tree of internal windows (GdkWindow). When a parent
        # widget contains child widgets -- like our preview popup containing
        # thumbnail EventBox widgets -- mouse movement between parent and
        # child generates crossing events.
        #
        # When the mouse moves from the popup background TO a thumbnail:
        #   - The popup receives leave-notify with detail=INFERIOR
        #   - This means "mouse went to a child widget"
        #   - The mouse is still visually inside the popup
        #
        # When the mouse moves genuinely outside the popup:
        #   - The popup receives leave-notify with detail=NONLINEAR
        #   - This means "mouse left for a completely different window"
        #
        # We ignore INFERIOR leaves because hiding the popup when the
        # user hovers over a thumbnail would make it impossible to
        # click on any thumbnail.
        if event.detail == Gdk.NotifyType.INFERIOR:
            log.debug("preview leave: INFERIOR (ignored)")
            return False
        log.debug(f"preview leave: detail={event.detail} mode={event.mode}")
        self._schedule_hide()
        return False

    def schedule_hide(self) -> None:
        """Public method for dock_window to start the hide timer."""
        log.debug("preview schedule_hide (from dock_window)")
        self._schedule_hide()

    def _schedule_hide(self, delay_ms: int = PREVIEW_HIDE_DELAY_MS) -> None:
        """Hide after a grace period (lets user move mouse to popup)."""
        self._cancel_hide_timer()
        log.debug(f"preview: scheduling hide in {delay_ms}ms")
        self._hide_timer_id = GLib.timeout_add(delay_ms, self._do_hide)

    def _do_hide(self) -> bool:
        log.debug("preview: hiding")
        self._hide_timer_id = 0
        self._current_desktop_id = ""
        self.hide()
        self._release_dock_autohide_if_needed()
        return False

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer_id:
            GLib.source_remove(self._hide_timer_id)
            self._hide_timer_id = 0

    def _release_dock_autohide_if_needed(self) -> None:
        """Let autohide continue once preview is gone and pointer is off-dock.

        Preview/autohide policy in simple terms:

        - preview visible => dock stays visible
        - preview hidden => dock may autohide
        - leaving dock should schedule preview hide when preview is visible
        - autohide should trigger when the preview actually finishes hiding,
          not at the first dock leave

        In practice this means PreviewPopup is the final authority for ending
        the combined dock+preview interaction. Dock leave only starts the
        preview grace timer. When the preview truly disappears, this method
        checks whether the pointer is already back on the dock; if it is not,
        autohide is allowed to continue.
        """
        if not self._autohide:
            return
        if self._pointer_inside_dock and self._pointer_inside_dock():
            return
        self._autohide.on_mouse_leave()

    @property
    def current_desktop_id(self) -> str:
        return self._current_desktop_id
