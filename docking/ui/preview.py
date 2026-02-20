"""Window preview popup — shows thumbnails of running windows on hover."""

from __future__ import annotations

from typing import TYPE_CHECKING

from docking.log import get_logger

log = get_logger("preview")

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
gi.require_version("Wnck", "3.0")
from gi.repository import Gtk, Gdk, GdkX11, GdkPixbuf, Wnck, GLib, Pango  # noqa: E402

if TYPE_CHECKING:
    from docking.platform.window_tracker import WindowTracker

THUMB_W = 200
THUMB_H = 150
POPUP_PADDING = 8
THUMB_SPACING = 8
LABEL_MAX_CHARS = 25

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
    color: rgba(255, 255, 255, 0.85);
    font-size: 11px;
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


_css_installed = False


def _ensure_css() -> None:
    global _css_installed
    if not _css_installed:
        _install_css()
        _css_installed = True


def capture_window(wnck_window: Wnck.Window, thumb_w: int = THUMB_W, thumb_h: int = THUMB_H) -> GdkPixbuf.Pixbuf | None:
    """Capture a window's content as a scaled thumbnail pixbuf."""
    if wnck_window.is_minimized():
        return _icon_fallback(wnck_window, thumb_w, thumb_h)

    xid = wnck_window.get_xid()
    display = GdkX11.X11Display.get_default()

    try:
        foreign = GdkX11.X11Window.foreign_new_for_display(display, xid)
    except Exception:
        foreign = None

    if foreign:
        try:
            w = foreign.get_width()
            h = foreign.get_height()
            if w > 0 and h > 0:
                pixbuf = Gdk.pixbuf_get_from_window(foreign, 0, 0, w, h)
                if pixbuf:
                    # Scale preserving aspect ratio
                    scale = min(thumb_w / w, thumb_h / h)
                    new_w = max(int(w * scale), 1)
                    new_h = max(int(h * scale), 1)
                    return pixbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)
        except Exception:
            pass

    return _icon_fallback(wnck_window, thumb_w, thumb_h)


def _icon_fallback(wnck_window: Wnck.Window, thumb_w: int, thumb_h: int) -> GdkPixbuf.Pixbuf | None:
    """Create a dark placeholder with the app icon centered."""
    icon = wnck_window.get_icon()
    if icon is None:
        return None

    # Create dark background
    bg = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, thumb_w, thumb_h)
    bg.fill(0x1E1E1EFF)

    # Center the icon
    icon_size = min(64, thumb_w, thumb_h)
    scaled_icon = icon.scale_simple(icon_size, icon_size, GdkPixbuf.InterpType.BILINEAR)
    if scaled_icon:
        x = (thumb_w - icon_size) // 2
        y = (thumb_h - icon_size) // 2
        scaled_icon.composite(
            bg, x, y, icon_size, icon_size,
            x, y, 1.0, 1.0,
            GdkPixbuf.InterpType.BILINEAR, 255,
        )
    return bg


class PreviewPopup(Gtk.Window):
    """Floating popup showing window thumbnails for a dock item."""

    def __init__(self, tracker: WindowTracker) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        _ensure_css()

        self._tracker = tracker
        self._autohide = None  # set via set_autohide()
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

    def set_autohide(self, controller) -> None:
        self._autohide = controller

    def show_for_item(self, desktop_id: str, icon_x: float, icon_w: float, dock_y: int) -> None:
        """Show preview popup above a dock icon.

        Args:
            desktop_id: Which app's windows to show.
            icon_x: Absolute X of the icon's left edge on screen.
            icon_w: Width of the icon.
            dock_y: Absolute Y of the dock's top edge on screen.
        """
        windows = self._tracker.get_windows_for(desktop_id)
        if not windows:
            self.hide()
            return

        self._current_desktop_id = desktop_id
        self._cancel_hide_timer()

        # Remove old content
        child = self.get_child()
        if child:
            self.remove(child)

        # Build thumbnail row
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=THUMB_SPACING)
        hbox.set_margin_start(POPUP_PADDING)
        hbox.set_margin_end(POPUP_PADDING)
        hbox.set_margin_top(POPUP_PADDING)
        hbox.set_margin_bottom(POPUP_PADDING)

        for window in windows:
            thumb_widget = self._make_thumbnail(window)
            hbox.pack_start(thumb_widget, False, False, 0)

        self.add(hbox)

        # Measure size, position, then show (avoids flash at wrong position)
        hbox.show_all()
        preferred = hbox.get_preferred_size()[1]
        popup_w = max(preferred.width + 2 * POPUP_PADDING, 1)
        popup_h = max(preferred.height + 2 * POPUP_PADDING, 1)

        icon_center_x = icon_x + icon_w / 2
        popup_x = int(icon_center_x - popup_w / 2)
        popup_y = int(dock_y - popup_h - 6)

        # Clamp to screen
        screen = self.get_screen()
        screen_w = screen.get_width()
        popup_x = max(0, min(popup_x, screen_w - popup_w))
        popup_y = max(0, popup_y)

        self.move(popup_x, popup_y)
        self.show_all()

    def _make_thumbnail(self, window: Wnck.Window) -> Gtk.Widget:
        """Create a clickable thumbnail widget for a window."""
        event_box = Gtk.EventBox()
        event_box.get_style_context().add_class("preview-thumb")
        event_box.set_events(
            Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.ENTER_NOTIFY_MASK
        )
        event_box.connect("button-press-event", self._on_thumb_click, window)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        # Thumbnail image
        pixbuf = capture_window(window)
        if pixbuf:
            image = Gtk.Image.new_from_pixbuf(pixbuf)
        else:
            image = Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        image.set_size_request(THUMB_W, THUMB_H)
        vbox.pack_start(image, False, False, 0)

        # Window title
        title = window.get_name() or "Untitled"
        if len(title) > LABEL_MAX_CHARS:
            title = title[:LABEL_MAX_CHARS - 1] + "\u2026"
        label = Gtk.Label(label=title)
        label.get_style_context().add_class("preview-label")
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(LABEL_MAX_CHARS)
        vbox.pack_start(label, False, False, 0)

        event_box.add(vbox)
        return event_box

    def _on_thumb_click(self, widget: Gtk.EventBox, event: Gdk.EventButton, window: Wnck.Window) -> bool:
        """Activate the clicked window."""
        self._tracker.activate_window(window)
        self.hide()
        return True

    def _on_enter(self, widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        """Keep popup and dock visible while mouse is inside preview."""
        log.debug("preview enter: detail=%s mode=%s", event.detail, event.mode)
        self._cancel_hide_timer()
        if self._autohide:
            self._autohide.on_mouse_enter()
        return False

    def _on_leave(self, widget: Gtk.Widget, event: Gdk.EventCrossing) -> bool:
        """Start hide timer when mouse leaves popup."""
        # Ignore leave events caused by child widgets (e.g. hovering over a thumbnail)
        if event.detail == Gdk.NotifyType.INFERIOR:
            log.debug("preview leave: INFERIOR (ignored)")
            return False
        log.debug("preview leave: detail=%s mode=%s", event.detail, event.mode)
        self._schedule_hide()
        if self._autohide:
            self._autohide.on_mouse_leave()
        return False

    def schedule_hide(self) -> None:
        """Public method for dock_window to start the hide timer."""
        log.debug("preview schedule_hide (from dock_window)")
        self._schedule_hide()

    def _schedule_hide(self, delay_ms: int = 300) -> None:
        """Hide after a grace period (lets user move mouse to popup)."""
        self._cancel_hide_timer()
        log.debug("preview: scheduling hide in %dms", delay_ms)
        self._hide_timer_id = GLib.timeout_add(delay_ms, self._do_hide)

    def _do_hide(self) -> bool:
        log.debug("preview: hiding")
        self._hide_timer_id = 0
        self._current_desktop_id = ""
        self.hide()
        return GLib.SOURCE_REMOVE

    def _cancel_hide_timer(self) -> None:
        if self._hide_timer_id:
            GLib.source_remove(self._hide_timer_id)
            self._hide_timer_id = 0

    @property
    def current_desktop_id(self) -> str:
        return self._current_desktop_id
