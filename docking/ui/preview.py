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
- thumbnail capture and fallback logic,
- delayed hide of the preview popup,
- preview enter/leave tracking,
- window activation from thumbnail clicks,
- releasing autohide only when the preview truly stops being relevant.

It does not own:

- hover detection,
- the decision to arm preview show,
- dock-wide effective hover state,
- autohide animation math.

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

Preview thumbnails try to show real window contents, but X11 capture is not
perfectly reliable. Windows can disappear mid-capture, minimized windows may
have no usable pixels, and some captures come back effectively black.

So the capture pipeline is:

    Wnck.Window
      |
      +--> capture XID pixels if possible
      |
      +--> detect unavailable/black captures
      |
      +--> fall back to generic app icon on dark background

That fallback is intentional. A stable generic preview is better than a broken
or flashing thumbnail.

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

from docking.log import get_logger

log = get_logger(name="preview")

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Wnck", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GdkX11, GLib, Gtk, Wnck

from docking.core.position import Position, is_horizontal
from docking.ui.runtime import clamp_to_screen

if TYPE_CHECKING:
    from docking.platform.window_tracker import WindowTracker
    from docking.ui.autohide import AutoHideController

THUMB_W = 200
THUMB_H = 150
POPUP_PADDING = 8
THUMB_SPACING = 8
LABEL_MAX_CHARS = 25
PREVIEW_HIDE_DELAY_MS = 300
ICON_FALLBACK_SIZE = 64
PREVIEW_GAP_PX = 40
CAPTURE_SAMPLE_GRID_MAX = 8
CAPTURE_ALPHA_MIN = 8
CAPTURE_MAX_CHANNEL_THRESHOLD = 10
CAPTURE_AVERAGE_LUMA_THRESHOLD = 5

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


def capture_window(
    wnck_window: Wnck.Window, thumb_w: int = THUMB_W, thumb_h: int = THUMB_H
) -> GdkPixbuf.Pixbuf | None:
    """Capture a window's content as a scaled thumbnail pixbuf.

    Uses GdkX11.X11Window.foreign_new_for_display to create a GDK handle
    for the target XID, then reads pixels via Gdk.pixbuf_get_from_window.
    Falls back to _icon_fallback if the window is minimized (no pixel
    content available) or if the foreign window handle fails (e.g. the
    window was destroyed between detection and capture).
    """
    if wnck_window.is_minimized():
        return _icon_fallback(thumb_w=thumb_w, thumb_h=thumb_h)

    xid = wnck_window.get_xid()
    pixbuf = capture_xid(xid=xid, thumb_w=thumb_w, thumb_h=thumb_h)
    if pixbuf is None:
        return _icon_fallback(thumb_w=thumb_w, thumb_h=thumb_h)
    return pixbuf


def capture_xid(
    xid: int, thumb_w: int = THUMB_W, thumb_h: int = THUMB_H
) -> GdkPixbuf.Pixbuf | None:
    """Capture a window thumbnail by XID, avoiding direct Wnck object use."""
    display = GdkX11.X11Display.get_default()

    try:
        foreign = GdkX11.X11Window.foreign_new_for_display(display, xid)
    except (TypeError, GLib.Error) as exc:
        log.warning(f"Failed to create foreign X11 window for xid={xid}: {exc}")
        foreign = None

    if foreign:
        try:
            width = foreign.get_width()
            height = foreign.get_height()
            if width > 0 and height > 0:
                # Trap X11 errors: the window may be destroyed between
                # foreign_new_for_display and pixbuf_get_from_window,
                # causing a segfault in the C layer that Python can't catch.
                display.error_trap_push()
                pixbuf = Gdk.pixbuf_get_from_window(foreign, 0, 0, width, height)
                x_error = display.error_trap_pop()
                if x_error or not pixbuf:
                    log.debug(f"X11 capture failed for xid={xid} (error={x_error})")
                    return None
                if _looks_unavailable_capture(pixbuf=pixbuf):
                    log.debug(f"Capture looked unavailable (black) for xid={xid}")
                    return None
                # Scale preserving aspect ratio
                scale = min(thumb_w / width, thumb_h / height)
                new_width = max(int(width * scale), 1)
                new_height = max(int(height * scale), 1)
                return pixbuf.scale_simple(
                    new_width, new_height, GdkPixbuf.InterpType.BILINEAR
                )
        except (TypeError, GLib.Error) as exc:
            log.warning(f"Window preview capture failed for xid={xid}: {exc}")

    return None


def _looks_unavailable_capture(pixbuf: GdkPixbuf.Pixbuf) -> bool:
    """Detect near-black captures that should fallback to app icon."""
    try:
        width = int(pixbuf.get_width())
        height = int(pixbuf.get_height())
        channels = int(pixbuf.get_n_channels())
        rowstride = int(pixbuf.get_rowstride())
        has_alpha = bool(pixbuf.get_has_alpha())
        data = pixbuf.get_pixels()
    except (AttributeError, TypeError, ValueError):
        return False

    if width <= 0 or height <= 0 or channels < 3 or rowstride <= 0:
        return False
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return False

    sample_x = max(1, min(CAPTURE_SAMPLE_GRID_MAX, width))
    sample_y = max(1, min(CAPTURE_SAMPLE_GRID_MAX, height))
    max_channel = 0
    total_luma = 0
    count = 0

    for yi in range(sample_y):
        y = int((yi + 0.5) * height / sample_y)
        if y >= height:
            y = height - 1
        for xi in range(sample_x):
            x = int((xi + 0.5) * width / sample_x)
            if x >= width:
                x = width - 1
            p = y * rowstride + x * channels
            r = data[p]
            g = data[p + 1]
            b = data[p + 2]
            a = data[p + 3] if has_alpha and channels >= 4 else 255
            if a < CAPTURE_ALPHA_MIN:
                continue
            max_channel = max(max_channel, r, g, b)
            total_luma += (r + g + b) // 3
            count += 1

    if count == 0:
        return True

    avg_luma = total_luma / count
    return (
        max_channel < CAPTURE_MAX_CHANNEL_THRESHOLD
        and avg_luma < CAPTURE_AVERAGE_LUMA_THRESHOLD
    )


def _icon_fallback(thumb_w: int, thumb_h: int) -> GdkPixbuf.Pixbuf | None:
    """Create a dark placeholder pixbuf with the app icon centered.

    Used when the window is minimized or pixel capture fails. Composites
    a generic app icon (scaled to ICON_FALLBACK_SIZE) onto a dark background.
    """
    # Create dark background
    bg = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, thumb_w, thumb_h)
    bg.fill(0x1E1E1EFF)

    # Center a generic icon and avoid querying per-window class-group state.
    icon_theme = Gtk.IconTheme.get_default()
    if icon_theme is None:
        return bg

    icon_size = min(ICON_FALLBACK_SIZE, thumb_w, thumb_h)
    try:
        icon = icon_theme.load_icon("application-x-executable", icon_size, 0)
    except GLib.Error as exc:
        log.warning(f"Failed to load fallback preview icon: {exc}")
        icon = None

    if icon:
        scaled_icon = icon.scale_simple(
            icon_size, icon_size, GdkPixbuf.InterpType.BILINEAR
        )
    else:
        scaled_icon = None

    if scaled_icon is not None:
        x = (thumb_w - icon_size) // 2
        y = (thumb_h - icon_size) // 2
        scaled_icon.composite(
            bg,
            x,
            y,
            icon_size,
            icon_size,
            x,
            y,
            1.0,
            1.0,
            GdkPixbuf.InterpType.BILINEAR,
            255,
        )
    return bg


class PreviewPopup(Gtk.Window):
    """Floating popup showing window thumbnails for a dock item."""

    def __init__(self, window_tracker: WindowTracker) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        _ensure_css()

        self._tracker = window_tracker
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
        xids = self._tracker.get_xids_for(desktop_id)
        if not xids:
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

        for xid in xids:
            thumb_widget = self._make_thumbnail_for_xid(
                xid=xid, fallback_icon_name=icon_name
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
        popup_x, popup_y = clamp_to_screen(
            popup_x, popup_y, popup_width, popup_height, screen_w, screen_h
        )

        self.move(popup_x, popup_y)
        self.show_all()

    def _make_thumbnail_for_xid(self, xid: int, fallback_icon_name: str) -> Gtk.Widget:
        """Create a clickable thumbnail widget for a window XID."""
        event_box = Gtk.EventBox()
        event_box.get_style_context().add_class("preview-thumb")
        event_box.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
        )
        event_box.connect("button-press-event", self._on_thumb_click, xid)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # Thumbnail image
        pixbuf = capture_xid(xid=xid)
        if pixbuf:
            image = Gtk.Image.new_from_pixbuf(pixbuf)
        else:
            image = Gtk.Image.new_from_icon_name(
                fallback_icon_name, Gtk.IconSize.DIALOG
            )
        image.set_size_request(THUMB_W, THUMB_H)
        vbox.pack_start(image, False, False, 0)

        # Window title
        title = self._tracker.get_window_title_for_xid(xid=xid)
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
        self, _widget: Gtk.EventBox, _event: Gdk.EventButton, xid: int
    ) -> bool:
        """Activate the clicked window."""
        self._tracker.activate_xid(xid=xid)
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
