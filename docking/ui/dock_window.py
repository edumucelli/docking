"""Main dock window -- GTK window with X11 dock hints, struts, and event wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib, GdkX11  # noqa: E402

from docking.core.position import Position, is_horizontal
from docking.platform.struts import set_dock_struts, clear_struts
from docking.core.zoom import compute_layout, content_bounds
from docking.applets.base import is_applet
from docking.platform.launcher import launch
from docking.ui.autohide import HideState
from docking.ui.tooltip import TooltipManager
from docking.ui.hover import HoverManager

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.zoom import LayoutItem
    from docking.platform.model import DockModel, DockItem
    from docking.ui.renderer import DockRenderer
    from docking.core.theme import Theme
    from docking.ui.autohide import AutoHideController
    from docking.platform.window_tracker import WindowTracker
    from docking.ui.dnd import DnDHandler
    from docking.ui.menu import MenuHandler
    from docking.ui.preview import PreviewPopup


CLICK_DRAG_THRESHOLD = 10  # px movement to distinguish click from drag
TRIGGER_PX = 2  # trigger strip size at screen edge
TRIGGER_PX_TOP = 8  # wider trigger at top (no physical edge barrier)


def should_keep_cursor_on_leave(autohide_enabled: bool, preview_visible: bool) -> bool:
    """Whether to preserve cursor position when mouse leaves the dock.

    True when autohide is active (smooth zoom decay during hide animation)
    or preview popup is visible (mouse moved into preview, zoom should hold).
    """
    return autohide_enabled or preview_visible


def compute_input_rect(
    pos: Position,
    window_w: int,
    window_h: int,
    content_offset: int,
    content_w: int,
    content_cross: int,
    autohide_state: HideState | None,
) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for the input shape region.

    When hidden: thin trigger strip at screen edge.
    When showing: full window (prevents oscillation during animation).
    Otherwise: content rectangle at the screen edge. The cross-axis
    extent is content_cross (icon area height), not the full window,
    so hovering the headroom above icons triggers a leave event --
    matching Plank's behavior where tooltips don't keep the dock visible.
    """
    if autohide_state in (HideState.HIDDEN, HideState.HIDING):
        trigger = TRIGGER_PX_TOP if pos == Position.TOP else TRIGGER_PX
        if pos == Position.BOTTOM:
            return (0, window_h - trigger, window_w, trigger)
        elif pos == Position.TOP:
            return (0, 0, window_w, trigger)
        elif pos == Position.LEFT:
            return (0, 0, trigger, window_h)
        else:
            return (window_w - trigger, 0, trigger, window_h)

    if autohide_state == HideState.SHOWING:
        return (0, 0, window_w, window_h)

    # VISIBLE or autohide off: content rect at screen edge.
    # Only covers icon area (content_cross), not headroom.
    cross = max(content_cross, 1)
    main = max(content_w, 1)
    if pos == Position.BOTTOM:
        return (content_offset, window_h - cross, main, cross)
    elif pos == Position.TOP:
        return (content_offset, 0, main, cross)
    elif pos == Position.LEFT:
        return (0, content_offset, cross, main)
    else:  # RIGHT
        return (window_w - cross, content_offset, cross, main)


# X11 mouse button codes
MOUSE_LEFT = 1
MOUSE_MIDDLE = 2
MOUSE_RIGHT = 3


class DockWindow(Gtk.Window):
    """Dock window positioned at screen bottom with X11 DOCK type hints."""

    def __init__(
        self,
        config: Config,
        model: DockModel,
        renderer: DockRenderer,
        theme: Theme,
        window_tracker: WindowTracker,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.config = config
        self.model = model
        self.renderer = renderer
        self.theme = theme
        self.window_tracker = window_tracker
        self.cursor_x: float = -1.0
        self.cursor_y: float = -1.0
        self.autohide: AutoHideController | None = None
        self._dnd: DnDHandler | None = None
        self._menu: MenuHandler | None = None
        self._preview: PreviewPopup | None = None
        self._tooltip = TooltipManager(self, config, model, theme)
        self._hover = HoverManager(self, config, model, theme, self._tooltip)

        self._setup_window()
        self._setup_drawing_area()
        self._connect_model()

    def _setup_window(self) -> None:
        """Configure window as an X11 dock."""
        self.set_title("Docking")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.stick()
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_app_paintable(True)

        # Enable RGBA visual for transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.connect("realize", self._on_realize)
        self.connect("destroy", Gtk.main_quit)

    def _setup_drawing_area(self) -> None:
        """Create the drawing surface and wire events."""
        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.BUTTON1_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.SCROLL_MASK
        )
        self.drawing_area.connect("draw", self._on_draw)
        self.drawing_area.connect("motion-notify-event", self._on_motion)
        self.drawing_area.connect("button-press-event", self._on_button_press)
        self.drawing_area.connect("button-release-event", self._on_button_release)
        self.drawing_area.connect("leave-notify-event", self._on_leave)
        self.drawing_area.connect("enter-notify-event", self._on_enter)
        self.drawing_area.connect("scroll-event", self._on_scroll)
        self.add(self.drawing_area)

        self._click_x: float = -1.0
        self._click_y: float = -1.0
        self._click_button: int = 0

    def _connect_model(self) -> None:
        """Listen for model changes to trigger redraws."""
        self.model.on_change = self._on_model_changed

    def set_autohide_controller(self, controller: AutoHideController) -> None:
        self.autohide = controller

    def set_dnd_handler(self, handler: DnDHandler) -> None:
        self._dnd = handler

    def set_menu_handler(self, handler: MenuHandler) -> None:
        self._menu = handler

    def set_preview_popup(self, preview: PreviewPopup) -> None:
        self._preview = preview
        self._hover.set_preview(preview)

    def _on_realize(self, _widget: Gtk.Widget) -> None:
        """Position dock and set struts after window is realized."""
        self._position_dock()
        self._set_struts()
        self._update_input_region()

    def _position_dock(self) -> None:
        """Position the dock window at the configured screen edge.

        The window spans the full monitor extent along its main axis
        (width for horizontal, height for vertical) to prevent resize
        wobble during zoom. The cross-axis dimension accommodates the
        max zoomed icon size plus padding and bounce headroom.
        """
        display = self.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()
        # Work area excludes other panels (e.g. MATE panel) so we don't
        # overlap them. Use full monitor geometry only for the edge where
        # we place the dock (we are a panel), work area for the other axis.
        workarea = monitor.get_workarea()

        icon_size = self.config.icon_size
        zoom = self.config.zoom_percent if self.config.zoom_enabled else 1.0
        bounce_headroom = int(icon_size * self.theme.urgent_bounce_height)
        cross = int(
            icon_size * zoom
            + self.theme.top_padding
            + self.theme.bottom_padding
            + bounce_headroom
        )

        pos = self.config.pos
        if is_horizontal(pos):
            # Span full monitor width; use workarea Y for positioning
            # to avoid overlapping panels on perpendicular edges
            win_w, win_h = geom.width, cross
            if pos == Position.BOTTOM:
                win_x = geom.x
                win_y = geom.y + geom.height - win_h
            else:  # TOP
                win_x = geom.x
                win_y = workarea.y
        else:
            # Span workarea height to avoid overlapping top/bottom panels
            win_w, win_h = cross, workarea.height
            if pos == Position.LEFT:
                win_x = geom.x
                win_y = workarea.y
            else:  # RIGHT
                win_x = geom.x + geom.width - win_w
                win_y = workarea.y

        self.set_size_request(win_w, win_h)
        self.resize(win_w, win_h)
        self.move(win_x, win_y)

    def _set_struts(self) -> None:
        """Reserve screen space for the dock via _NET_WM_STRUT_PARTIAL."""
        if self.config.autohide:
            self._clear_struts()
            return

        gdk_window = self.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return

        display = self.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()
        screen = self.get_screen()

        # Reserve space for the full icon height + bottom padding so
        # windows sit above the icons, not just above the shelf.
        # compute_dock_size returns shelf-based height (with negative
        # top_padding), but struts need the visible icon extent.
        icon_size = self.config.icon_size
        strut_height = int(icon_size + self.theme.bottom_padding)

        set_dock_struts(gdk_window, strut_height, geom, screen, self.config.pos)

    def _clear_struts(self) -> None:
        """Remove strut reservation by setting all struts to zero."""
        gdk_window = self.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return
        clear_struts(gdk_window)

    def update_struts(self) -> None:
        """Public method to refresh struts after autohide toggle.

        Called from the menu when the user switches between autohide
        and always-visible modes. Immediately updates the X11 strut
        reservation so the window manager resizes application windows.
        """
        self._set_struts()

    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        """Render the dock via the renderer."""
        hide_offset = self.autohide.hide_offset if self.autohide else 0.0
        # The renderer receives zoom_progress from the autohide controller.
        # During normal hover (no autohide), zoom_progress is 1.0 and has
        # no effect. During a hide animation, zoom_progress decays from 1.0
        # toward 0.0, smoothly reducing icon scales.
        #
        # After the hide animation completes (state=HIDDEN), we finally
        # reset cursor_x to -1.0. This is deferred from _on_leave to allow
        # the smooth zoom decay described above.
        # zoom_progress is only relevant during autohide animations.
        # When autohide is disabled, zoom should always be at full strength.
        if self.autohide and self.autohide.enabled:
            zoom_progress = self.autohide.zoom_progress
        else:
            zoom_progress = 1.0
        drag_index = self._dnd.drag_index if self._dnd else -1
        drop_insert = self._dnd.drop_insert_index if self._dnd else -1
        hovered_id = (
            self._hover.hovered_item.desktop_id
            if self._hover and self._hover.hovered_item
            else ""
        )
        main_cursor = self._main_axis_cursor()
        self.renderer.draw(
            cr,
            widget,
            self.model,
            self.config,
            self.theme,
            main_cursor,
            hide_offset,
            drag_index,
            drop_insert,
            zoom_progress,
            hovered_id,
        )
        # Update input region as hide state changes (shrink when hidden)
        self._update_input_region()

        # Reset cursor after hide completes
        if self.autohide and self.autohide.state == HideState.HIDDEN:
            self.cursor_x = -1.0
            self.cursor_y = -1.0

        # Keep redraw pump alive while urgent glow is visible (dock hidden)
        if self._has_active_urgent_glow():
            GLib.timeout_add(16, self._urgent_glow_tick)

        return True

    def _on_motion(self, widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        """Update cursor position and trigger zoom redraw."""
        self.cursor_x = event.x
        self.cursor_y = event.y
        self._update_dock_size()
        widget.queue_draw()
        self._hover.update(self._main_axis_cursor())
        return False  # Propagate so GTK drag source can detect drag threshold

    def _on_button_press(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        """Record press position for click vs drag discrimination."""
        self._click_x = event.x
        self._click_y = event.y
        self._click_button = event.button
        return False  # Propagate so DnD can still work

    def _on_button_release(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        """Handle clicks on dock items (on release to avoid DnD conflicts)."""
        # Only act if release is near the press point (not a drag)
        if is_horizontal(self.config.pos):
            drag_delta = abs(event.x - self._click_x)
        else:
            drag_delta = abs(event.y - self._click_y)
        if drag_delta > CLICK_DRAG_THRESHOLD:
            return False

        if event.button == MOUSE_RIGHT:
            if self._menu:
                self._menu.show(event, self._main_axis_cursor())
            return True

        if event.button in (MOUSE_LEFT, MOUSE_MIDDLE):
            layout = compute_layout(
                self.model.visible_items(),
                self.config,
                self.local_cursor_main(),
                item_padding=self.theme.item_padding,
                h_padding=self.theme.h_padding,
            )
            main_event = event.x if is_horizontal(self.config.pos) else event.y
            item = self.hit_test(main_event, layout)
            if item is None:
                return True

            # Animation trigger chain:
            #
            # Every click sets last_clicked, which triggers the click
            # darken animation (sine pulse, 300ms). The renderer reads
            # this timestamp each frame and computes the darken amount.
            #
            # If the click also launches the app (not already running,
            # or force-launch via middle-click/Ctrl+click), we also set
            # last_launched. This triggers the launch bounce animation
            # (600ms, two bounces). Both animations run simultaneously --
            # the icon darkens AND bounces at the same time.
            #
            # The two timestamps are independent fields on DockItem.
            # Setting last_clicked does not affect last_launched, and
            # vice versa. The renderer evaluates each independently.
            #
            # The anim pump duration is set to cover the longer of the
            # two animations plus a small margin for the final frame.
            now = GLib.get_monotonic_time()
            item.last_clicked = now

            # Applets handle their own click

            if is_applet(item.desktop_id):
                applet = self.model.get_applet(item.desktop_id)
                if applet:
                    applet.on_clicked()
                self._hover.start_anim_pump(350)
                return True

            force_launch = event.button == MOUSE_MIDDLE or (
                event.state & Gdk.ModifierType.CONTROL_MASK
            )
            if force_launch or not item.is_running:
                item.last_launched = now
                launch(item.desktop_id)
                self._hover.start_anim_pump(700)  # 600ms bounce + margin
            else:
                self.window_tracker.toggle_focus(item.desktop_id)
                self._hover.start_anim_pump(350)  # 300ms click darken

        return True

    def _on_scroll(self, _widget: Gtk.DrawingArea, event: Gdk.EventScroll) -> bool:
        """Forward scroll events to applet if scrolled item is one."""
        layout = compute_layout(
            self.model.visible_items(),
            self.config,
            self.local_cursor_main(),
            item_padding=self.theme.item_padding,
            h_padding=self.theme.h_padding,
        )
        main_event = event.x if is_horizontal(self.config.pos) else event.y
        item = self.hit_test(main_event, layout)
        if item and is_applet(item.desktop_id):
            applet = self.model.get_applet(item.desktop_id)
            if applet:
                direction_up = event.direction == Gdk.ScrollDirection.UP
                applet.on_scroll(direction_up)
                # Refresh tooltip immediately (item.name may have changed)
                self._tooltip.update(item, layout)
                return True
        return False

    def _on_leave(self, widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        """Handle mouse leaving the dock area.

        This is the most complex event handler in the dock because it
        coordinates several subsystems: zoom state, preview popups,
        autohide, and cursor tracking.
        """
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False

        self._hover.hovered_item = None
        self._hover.cancel()
        self._tooltip.hide()

        preview_visible = self._preview and self._preview.get_visible()
        if self._preview and not preview_visible:
            self._preview.schedule_hide()

        autohide_on = bool(self.autohide and self.autohide.enabled)
        if not should_keep_cursor_on_leave(autohide_on, bool(preview_visible)):
            self.cursor_x = -1.0
            self.cursor_y = -1.0

        self._update_dock_size()
        widget.queue_draw()
        if autohide_on and self.autohide:
            self.autohide.on_mouse_leave()
        return True

    def _on_enter(self, _widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        """Notify auto-hide on mouse enter and capture cursor position.

        Cursor position must be set here (not just in motion events)
        because during the SHOWING animation the zoom engine needs a
        valid cursor to compute the expanding displacement effect.
        Without this, cursor stays at -1 from the HIDDEN reset and
        compute_layout produces rest-only positions (no expansion).
        """
        self.cursor_x = event.x
        self.cursor_y = event.y
        if self.autohide:
            self.autohide.on_mouse_enter()
        return True

    def _has_active_urgent_glow(self) -> bool:
        """True if dock is hidden and any item has an active urgent glow."""
        if not self.autohide or self.autohide.state != HideState.HIDDEN:
            return False
        now = GLib.get_monotonic_time()
        glow_time_us = self.theme.urgent_glow_time_ms * 1000
        for item in self.model.visible_items():
            if item.last_urgent > 0 and (now - item.last_urgent) < glow_time_us:
                return True
        return False

    def _urgent_glow_tick(self) -> bool:
        """One-shot tick to keep redraws flowing during urgent glow."""
        self.drawing_area.queue_draw()
        return False  # don't repeat; _on_draw re-schedules if still needed

    def _on_model_changed(self) -> None:
        """Reposition and redraw when the model changes."""
        self._update_dock_size()
        self._hover.on_model_changed()
        self.drawing_area.queue_draw()

    def _update_dock_size(self) -> None:
        """Reposition dock only when item count changes (not during hover)."""
        self._update_input_region()

    def _update_input_region(self) -> None:
        """Define which part of the window responds to mouse events.

        GTK windows receive ALL mouse events (clicks, hover, scroll) within
        their pixel bounds. Since our dock window spans the full monitor
        width (to prevent resize wobble during zoom), the transparent area
        on either side of the dock icons would block clicks on desktop icons,
        taskbar items, or any other windows at the same Y coordinate.

        To solve this, we set an "input shape region" -- a pixel mask that
        tells the X11 window manager which parts of the window are "real."
        Clicks outside this region pass through to whatever is underneath,
        as if our window wasn't there.

        The input region is a rectangle covering only the dock content area:

          |<----------- monitor (1920px) ------------------>|
          |          |  [dock icons here]  |                |
          |          |<-- input region --->|                |
          |          |                     |                |
          | clicks   |  clicks handled     |  clicks pass   |
          | pass     |  by the dock        |  through to    |
          | through  |                     |  desktop       |

        We compute the region from the maximum zoom layout (cursor at center)
        to ensure the input area is generous enough to capture hover events
        even at the edges of the zoom spread.
        """
        gdk_window = self.get_window()
        if not gdk_window:
            return

        items = self.model.visible_items()
        n = len(items)
        icon_size = self.config.icon_size
        # Content width: use max-zoom width (cursor at center) for generous input area
        pad = self.theme.h_padding + self.theme.item_padding / 2
        base_w = pad * 2 + n * icon_size + max(0, n - 1) * self.theme.item_padding
        layout = compute_layout(
            items,
            self.config,
            base_w / 2,
            item_padding=self.theme.item_padding,
            h_padding=self.theme.h_padding,
        )
        left_edge, right_edge = content_bounds(
            layout, icon_size, self.theme.h_padding, self.theme.item_padding
        )
        content_w = right_edge - left_edge

        window_w: int = self.get_size()[0]
        window_h: int = self.get_size()[1]
        pos = self.config.pos
        horizontal = is_horizontal(pos)

        # Content centering along main axis
        main_size = window_w if horizontal else window_h
        content_offset = int((main_size - content_w) / 2 - left_edge)

        autohide_state = (
            self.autohide.state if self.autohide and self.autohide.enabled else None
        )
        # Interactive cross-axis extent: icon height + edge padding.
        # This excludes the headroom above icons (zoom/bounce space)
        # so hovering above icons triggers a leave -> dock hides.
        content_cross = int(icon_size + self.theme.bottom_padding)

        rx, ry, rw, rh = compute_input_rect(
            pos,
            window_w,
            window_h,
            content_offset,
            int(content_w),
            content_cross,
            autohide_state,
        )
        rect = cairo.RectangleInt(rx, ry, rw, rh)
        region = cairo.Region(rect)
        gdk_window.input_shape_combine_region(region, 0, 0)

    # --- Coordinate Conversion Utilities ---
    #
    # All layout is computed in 1D along the dock's "main axis" (the
    # axis along which icons are arranged). For horizontal docks (top/
    # bottom), this is the X axis. For vertical docks (left/right),
    # this is the Y axis. The "cross axis" is perpendicular.
    #
    # These methods convert between window-space and content-space
    # along the main axis.

    def _main_axis_cursor(self) -> float:
        """Cursor position along the dock's main axis (window-space)."""
        if is_horizontal(self.config.pos):
            return self.cursor_x
        return self.cursor_y

    def _main_axis_window_size(self) -> int:
        """Window extent along the dock's main axis."""
        w, h = self.get_size()
        return int(w if is_horizontal(self.config.pos) else h)

    def _base_main_offset(self) -> float:
        """Offset to center base (no-zoom) content along the main axis."""
        n = len(self.model.visible_items())
        pad = self.theme.h_padding + self.theme.item_padding / 2
        base_w = (
            pad * 2
            + n * self.config.icon_size
            + max(0, n - 1) * self.theme.item_padding
        )
        return (self._main_axis_window_size() - base_w) / 2

    def local_cursor_main(self) -> float:
        """Cursor in content-space along the main axis."""
        mc = self._main_axis_cursor()
        if mc < 0:
            return -1.0
        return mc - self._base_main_offset()

    def zoomed_main_offset(self, layout: list[LayoutItem]) -> float:
        """Main-axis offset matching where icons are actually rendered."""
        left_edge, right_edge = content_bounds(
            layout,
            self.config.icon_size,
            self.theme.h_padding,
            self.theme.item_padding,
        )
        zoomed_w = right_edge - left_edge
        return (self._main_axis_window_size() - zoomed_w) / 2 - left_edge

    # Keep short aliases used by other modules
    def local_cursor_x(self) -> float:
        """Alias for local_cursor_main (backward compat)."""
        return self.local_cursor_main()

    def zoomed_x_offset(self, layout: list[LayoutItem]) -> float:
        """Alias for zoomed_main_offset (backward compat)."""
        return self.zoomed_main_offset(layout)

    def hit_test(self, main_coord: float, layout: list[LayoutItem]) -> DockItem | None:
        """Find which DockItem is under the cursor along the main axis."""
        offset = self.zoomed_main_offset(layout)
        items = self.model.visible_items()
        for i, li in enumerate(layout):
            icon_w = li.scale * self.config.icon_size
            left = li.x + offset
            right = left + icon_w
            if left <= main_coord <= right:
                return items[i]
        return None

    def reposition(self) -> None:
        """Re-layout after position change -- reposition window, struts, input."""
        self._position_dock()
        self._set_struts()
        self._update_input_region()
        self.drawing_area.queue_draw()

    def queue_redraw(self) -> None:
        """Convenience for external controllers to trigger redraw."""
        self.drawing_area.queue_draw()
