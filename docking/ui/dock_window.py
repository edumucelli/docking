"""Main dock window and interaction hub.

DockWindow is the top-level GTK window that hosts the dock drawing area and
coordinates most UI subsystems: rendering, hover tracking, tooltip/preview
behavior, drag-and-drop, menus, autohide, and X11 panel integration.

What kind of window this is

This is not a normal application window. It is created with X11 dock/panel
hints (``WindowTypeHint.DOCK``), marked sticky and keep-above, and usually
placed at a screen edge. In always-visible mode it also publishes struts so
the window manager reserves screen space for it.

Two important geometries

DockWindow intentionally separates:

1. visual window geometry
2. interactive input geometry

The GTK window is sized to a stable edge-aligned box (often spanning the full
main axis) to avoid resize wobble during zoom and animation. But only a subset
of that window should intercept mouse input. Transparent areas around icons
must pass through to the desktop/applications beneath.

To achieve this, the code uses an X11 input shape region (via
``input_shape_combine_region``). This makes the dock feel visually smooth while
remaining non-obstructive for clicks outside the actual icon area.

Main-axis model used throughout

Dock math is orientation-agnostic by projecting everything onto a single
"main axis":

- top/bottom dock: main axis is X
- left/right dock: main axis is Y

Zoom layout, hit-testing, drag insertion, and hover decisions all use this
shared axis mapping. That keeps behavior consistent across orientations.

How a frame is produced

For each draw cycle:

1. compute current autohide/zoom parameters,
2. ask renderer to draw with current cursor state and drag state,
3. refresh input region if hide state/content bounds changed,
4. schedule additional redraw ticks only when needed (urgent glow, animations).

This means draw is both a rendering path and a place where window-level
interaction masks stay in sync with the current dock state.

Event flow responsibilities

DockWindow owns raw GTK pointer/button/scroll/enter/leave events and delegates
high-level behavior:

- hover updates go through HoverManager,
- right-click context menus go through MenuHandler,
- drag behavior goes through DnDHandler,
- app launch/focus goes through launcher + WindowTracker,
- applet clicks/scrolls are delegated to applet instances.

Keeping this wiring in one class prevents conflicting event ownership and makes
cross-feature behavior (for example autohide + previews + drag) predictable.

Autohide-specific behavior

When autohide is enabled, DockWindow participates in a small state machine:

- visible/showing: input region covers icon content,
- hidden: input region shrinks to a thin edge trigger strip,
- transitions: cursor and zoom state are preserved long enough for smooth
  animation instead of abrupt resets.

This is why some cursor resets and input-shape updates are intentionally tied
to draw/autohide state rather than immediate enter/leave events.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk  # noqa: E402

from docking.applets.base import is_applet
from docking.core.position import Position, is_horizontal
from docking.core.zoom import compute_layout, content_bounds
from docking.log import get_logger
from docking.platform.launcher import launch
from docking.platform.struts import clear_struts, set_dock_struts
from docking.ui.autohide import HideState
from docking.ui.hover import HoverManager
from docking.ui.tooltip import TooltipManager

_log = get_logger(name="dock_window")

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.core.theme import Theme
    from docking.core.zoom import LayoutItem
    from docking.platform.model import DockModel
    from docking.platform.window_tracker import WindowTracker
    from docking.ui.autohide import AutoHideController
    from docking.ui.dnd import DnDHandler
    from docking.ui.menu import MenuHandler
    from docking.ui.preview import PreviewPopup
    from docking.ui.renderer import DockRenderer


# Minimum pixel movement between press and release to consider it a drag
# rather than a click. Prevents accidental launches when slightly moving
# the mouse during a click.
CLICK_DRAG_THRESHOLD = 10

# Thin input region at screen edge when dock is hidden, allowing the
# mouse to re-enter and trigger the show animation.
TRIGGER_PX = 2
# Top edge needs a wider trigger because there's no physical screen edge
# barrier -- the mouse can overshoot into a top panel more easily.
TRIGGER_PX_TOP = 8


class Rect(NamedTuple):
    """Rectangle with named x, y, w, h fields (input region, etc.)."""

    x: int
    y: int
    w: int
    h: int


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
) -> Rect:
    """Return (x, y, w, h) for the input shape region.

    Two-state approach: HIDDEN uses a thin trigger strip at the screen edge
    for reveal; all other states (including autohide off) use the content rect.
    No interpolation during animation -- this prevents oscillation from
    mouse re-entry events during hide/show transitions.
    """
    # Determine cross-axis size:
    # - Autohide off or non-HIDDEN: content rect
    # - HIDDEN: thin trigger strip at screen edge for reveal
    #
    # During HIDING, keeping the content rect prevents oscillation: the
    # mouse stays inside so no re-enter events fire. Once fully hidden,
    # it switches to the trigger strip. During SHOWING, content rect
    # ensures the mouse stays "inside" until the dock is fully visible.
    if autohide_state == HideState.HIDDEN:
        trigger = TRIGGER_PX_TOP if pos == Position.TOP else TRIGGER_PX
        cross = trigger
    else:
        cross = max(content_cross, 1)
    main = max(content_w, 1)

    if pos == Position.BOTTOM:
        return Rect(content_offset, window_h - cross, main, cross)
    elif pos == Position.TOP:
        return Rect(content_offset, 0, main, cross)
    elif pos == Position.LEFT:
        return Rect(0, content_offset, cross, main)
    else:
        return Rect(window_w - cross, content_offset, cross, main)


# X11 mouse button codes
MOUSE_LEFT = 1
MOUSE_MIDDLE = 2
MOUSE_RIGHT = 3


class DockWindow(Gtk.Window):
    """Top-level dock window that owns event routing and edge integration.

    DockWindow acts as the composition root for runtime UI behavior: it keeps
    references to model, renderer, hover manager, tooltip manager, preview,
    dnd, menu, and optional autohide controller, then coordinates them from
    GTK event callbacks.

    In short: renderer draws pixels, model owns item state, and DockWindow
    turns pointer/window-manager events into the right calls between them.
    """

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
        self._menu_popup_visible: bool = False
        self._preview: PreviewPopup | None = None
        self._tooltip = TooltipManager(self, config, model, theme)
        self._hover = HoverManager(self, config, model, theme, self._tooltip)

        self._setup_window()
        self._setup_drawing_area()
        self._connect_model()

    def _setup_window(self) -> None:
        """Configure GTK window as an X11 dock.

        Sets window manager hints (DOCK type, skip taskbar/pager, keep above,
        sticky) and enables RGBA visual for composited transparency.
        """
        self.set_title("Docking")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.stick()
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_app_paintable(True)
        self.set_resizable(False)

        # Enable RGBA visual for transparency
        screen = self.get_screen()
        visual = screen.get_rgba_visual() or screen.get_system_visual()
        self.set_visual(visual)

        self.connect("realize", self._on_realize)
        self.connect("destroy", Gtk.main_quit)

    def _setup_drawing_area(self) -> None:
        """Create the drawing surface and wire all mouse/scroll event handlers.

        Disables GTK double buffering (we blit from an offscreen surface
        instead) and registers handlers for draw, motion, button, enter/leave,
        and scroll events.
        """
        self.drawing_area = Gtk.DrawingArea()
        # Disable GTK's automatic double buffering. For transparent RGBA
        # windows, GTK allocates an intermediate CPU buffer that causes
        # CPU->GPU transfer stalls visible as flicker during fast mouse
        # movement. Direct rendering to the X11 backing surface avoids this.
        self.drawing_area.set_double_buffered(False)
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
        self._last_input_rect: Rect | None = None

    def _connect_model(self) -> None:
        """Listen for model changes to trigger redraws."""
        self.model.on_change = self._on_model_changed

    def set_autohide_controller(self, controller: AutoHideController) -> None:
        self.autohide = controller

    def set_dnd_handler(self, handler: DnDHandler) -> None:
        self._dnd = handler

    def set_menu_handler(self, handler: MenuHandler) -> None:
        self._menu = handler

    def on_menu_popup_opened(self) -> None:
        """Track that a dock context menu popup is currently active."""
        self._menu_popup_visible = True

    def on_menu_popup_closed(self) -> None:
        """Reconcile autohide state when the context menu closes."""
        if not self._menu_popup_visible:
            return
        self._menu_popup_visible = False

        if not self.autohide or not self.autohide.enabled:
            return
        if self._pointer_inside_input_rect():
            return

        self._hover.hovered_item = None
        self._hover.cancel()
        self._tooltip.hide()

        preview_visible = self._preview and self._preview.get_visible()
        if self._preview and not preview_visible:
            self._preview.schedule_hide()

        self._update_dock_size()
        self.drawing_area.queue_draw()
        self.autohide.on_mouse_leave()

    def _pointer_inside_input_rect(self) -> bool:
        """Return True when pointer is inside current dock input region."""
        if self._last_input_rect is None or not self.get_realized():
            return False
        display = self.get_display()
        if not display:
            return False
        seat = display.get_default_seat()
        if not seat:
            return False
        pointer = seat.get_pointer()
        if not pointer:
            return False
        try:
            _, screen_x, screen_y = pointer.get_position()
            win_x, win_y = self.get_position()
        except Exception:
            return False

        local_x = screen_x - win_x
        local_y = screen_y - win_y
        rx, ry, rw, rh = self._last_input_rect
        return rx <= local_x <= rx + rw and ry <= local_y <= ry + rh

    def set_preview_popup(self, preview: PreviewPopup) -> None:
        self._preview = preview
        self._hover.set_preview(preview)

    def current_monitor_choice(self) -> int:
        """Current monitor menu selection (-1=primary, >=0 specific monitor)."""
        display = self.get_display()
        if not display:
            return -1
        n_monitors = display.get_n_monitors()
        if n_monitors <= 0:
            return -1
        selected = int(getattr(self.config, "monitor_index", -1))
        if selected == -1:
            return self.primary_monitor_index()
        if selected < 0 or selected >= n_monitors:
            return self.primary_monitor_index()
        return selected

    def get_monitor_menu_choices(self) -> list[tuple[str, int]]:
        """Monitor choices for menu display. Empty when only one monitor."""
        display = self.get_display()
        if not display:
            return []
        n_monitors = display.get_n_monitors()
        if n_monitors <= 1:
            return []

        primary = display.get_primary_monitor() or display.get_monitor(0)
        primary_idx = 0
        for idx in range(n_monitors):
            if display.get_monitor(idx) is primary:
                primary_idx = idx
                break

        choices: list[tuple[str, int]] = []
        for idx in range(n_monitors):
            monitor = display.get_monitor(idx)
            if monitor is None:
                continue
            geom = monitor.get_geometry()
            label = f"Display {idx + 1}: {geom.width}x{geom.height}"
            if idx == primary_idx:
                label += " (Primary)"
            choices.append((label, idx))
        return choices

    def primary_monitor_index(self) -> int:
        """Index of primary monitor, or zero as a stable fallback."""
        display = self.get_display()
        if not display:
            return 0
        n_monitors = display.get_n_monitors()
        if n_monitors <= 0:
            return 0
        primary = display.get_primary_monitor() or display.get_monitor(0)
        for idx in range(n_monitors):
            if display.get_monitor(idx) is primary:
                return idx
        return 0

    def _resolve_target_monitor(self, display: Gdk.Display) -> Gdk.Monitor | None:
        """Resolve configured monitor, falling back to primary monitor."""
        get_n = getattr(display, "get_n_monitors", None)
        if not callable(get_n):
            return display.get_primary_monitor() or display.get_monitor(0)

        n_monitors = get_n()
        if n_monitors <= 0:
            return None

        selected = int(getattr(self.config, "monitor_index", -1))
        if 0 <= selected < n_monitors:
            monitor = display.get_monitor(selected)
            if monitor is not None:
                return monitor

        return display.get_primary_monitor() or display.get_monitor(0)

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
        monitor = DockWindow._resolve_target_monitor(self, display=display)
        if monitor is None:
            return
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
        if is_horizontal(pos=pos):
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

        _log.debug(
            "dock position: win=(%d,%d) size=%dx%d cross=%d bounce_headroom=%d",
            win_x,
            win_y,
            win_w,
            win_h,
            cross,
            bounce_headroom,
        )
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
        monitor = DockWindow._resolve_target_monitor(self, display=display)
        if monitor is None:
            return
        geom = monitor.get_geometry()
        screen = self.get_screen()

        # Reserve space for the full icon height + bottom padding so
        # windows sit above the icons, not just above the shelf.
        # compute_dock_size returns shelf-based height (with negative
        # top_padding), but struts need the visible icon extent.
        icon_size = self.config.icon_size
        strut_height = int(icon_size + self.theme.bottom_padding)

        set_dock_struts(
            gdk_window=gdk_window,
            dock_height=strut_height,
            monitor_geom=geom,
            screen=screen,
            position=self.config.pos,
        )

    def _clear_struts(self) -> None:
        """Remove strut reservation by setting all struts to zero."""
        gdk_window = self.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return
        clear_struts(gdk_window=gdk_window)

    def update_struts(self) -> None:
        """Public method to refresh struts after autohide toggle.

        Called from the menu when the user switches between autohide
        and always-visible modes. Immediately updates the X11 strut
        reservation so the window manager resizes application windows.
        """
        self._set_struts()

    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        """GTK draw signal handler -- orchestrates each frame.

        Delegates rendering to the DockRenderer, then updates the input
        region (which may change during hide animation), resets cursor
        after hide completes, and re-schedules redraws for urgent glow.
        """
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
        """Update cursor position, trigger zoom redraw, and refresh hover state.

        Returns False to propagate the event so GTK's drag source can
        detect the drag threshold and initiate DnD when appropriate.
        """
        self.cursor_x = event.x
        self.cursor_y = event.y
        self._update_dock_size()
        widget.queue_draw()
        self._hover.update(self._main_axis_cursor())
        return False  # Propagate so GTK drag source can detect drag threshold

    def _on_button_press(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        """Record press position for click-vs-drag discrimination.

        The actual click action fires on button-release, not here. This
        handler only stores the press coordinates so _on_button_release
        can compare them and distinguish clicks from drags.
        """
        self._click_x = event.x
        self._click_y = event.y
        self._click_button = event.button
        return False  # Propagate so DnD can still work

    def _on_button_release(
        self, _widget: Gtk.DrawingArea, event: Gdk.EventButton
    ) -> bool:
        """Handle clicks on dock items (on release to avoid DnD conflicts)."""
        # Only act if release is near the press point (not a drag)
        if is_horizontal(pos=self.config.pos):
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
            main_event = event.x if is_horizontal(pos=self.config.pos) else event.y
            item = self.hit_test(main_coord=main_event, layout=layout)
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

            if is_applet(desktop_id=item.desktop_id):
                applet = self.model.get_applet(item.desktop_id)
                if applet:
                    applet.on_clicked()
                    # Refresh tooltip immediately so applet name/tooltip
                    # changes are visible without waiting for pointer motion.
                    self._tooltip.update(item, layout)
                self._hover.start_anim_pump(350)
                return True

            force_launch = event.button == MOUSE_MIDDLE or (
                event.state & Gdk.ModifierType.CONTROL_MASK
            )
            if force_launch or not item.is_running:
                item.last_launched = now
                launch(desktop_id=item.desktop_id)
                self._hover.start_anim_pump(700)  # 600ms bounce + margin
            else:
                self.window_tracker.toggle_focus(item.desktop_id)
                self._hover.start_anim_pump(350)  # 300ms click darken

        return True

    def _on_scroll(self, _widget: Gtk.DrawingArea, event: Gdk.EventScroll) -> bool:
        """Forward scroll events to the applet under the cursor, if any.

        Hit-tests the cursor against the current layout to find the target
        item. If it's an applet, delegates to its on_scroll() and refreshes
        the tooltip (applet name/state may change on scroll, e.g. clippy).
        """
        layout = compute_layout(
            self.model.visible_items(),
            self.config,
            self.local_cursor_main(),
            item_padding=self.theme.item_padding,
            h_padding=self.theme.h_padding,
        )
        main_event = event.x if is_horizontal(pos=self.config.pos) else event.y
        item = self.hit_test(main_coord=main_event, layout=layout)
        if item and is_applet(desktop_id=item.desktop_id):
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
        _log.debug(
            "leave: detail=%s mode=%s x=%.0f y=%.0f",
            event.detail,
            event.mode,
            event.x,
            event.y,
        )
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False

        # Spurious leave filter: when the tooltip popup appears, GTK
        # generates a NONLINEAR leave even though the cursor is still
        # inside the dock's input region. Check cursor against the
        # current input rect and ignore if inside.
        if self._last_input_rect is not None:
            rx, ry, rw, rh = self._last_input_rect
            if rx <= event.x <= rx + rw and ry <= event.y <= ry + rh:
                return False

        self._hover.hovered_item = None
        self._hover.cancel()
        self._tooltip.hide()

        preview_visible = self._preview and self._preview.get_visible()
        if self._preview and not preview_visible:
            self._preview.schedule_hide()

        autohide_on = bool(self.autohide and self.autohide.enabled)
        if not should_keep_cursor_on_leave(
            autohide_enabled=autohide_on, preview_visible=bool(preview_visible)
        ):
            self.cursor_x = -1.0
            self.cursor_y = -1.0

        self._update_dock_size()
        widget.queue_draw()
        if autohide_on and self.autohide:
            self.autohide.on_mouse_leave()
        return True

    def _on_enter(self, _widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        """Handle mouse entering the dock -- trigger reveal and capture cursor.

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
        # Refresh hover/tooltip state even without mouse motion so applets
        # that update item.name asynchronously (e.g. workspace switcher)
        # show the new tooltip text immediately.
        if self._hover.hovered_item is not None:
            self._hover.update(self._main_axis_cursor())
        self.drawing_area.queue_draw()

    def _update_dock_size(self) -> None:
        """Refresh the input region after model or cursor changes.

        The window itself doesn't resize (it spans the full monitor),
        but the clickable input region needs to track the content bounds.
        """
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
        icon_size = self.config.icon_size
        # Use rest layout (no zoom) for input region bounds. The max-zoom
        # layout was too generous and prevented hide when mouse moved past
        # the last icon on the right.
        layout = compute_layout(
            items,
            self.config,
            -1e6,  # sentinel: no cursor -> rest positions
            item_padding=self.theme.item_padding,
            h_padding=self.theme.h_padding,
        )
        left_edge, right_edge = content_bounds(
            layout=layout,
            icon_size=icon_size,
            h_padding=self.theme.h_padding,
            item_padding=self.theme.item_padding,
        )
        content_w = right_edge - left_edge

        window_w: int = self.get_size()[0]
        window_h: int = self.get_size()[1]
        pos = self.config.pos
        horizontal = is_horizontal(pos=pos)

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

        new_rect = compute_input_rect(
            pos=pos,
            window_w=window_w,
            window_h=window_h,
            content_offset=content_offset,
            content_w=int(content_w),
            content_cross=content_cross,
            autohide_state=autohide_state,
        )
        if new_rect != self._last_input_rect:
            self._last_input_rect = new_rect
            region = cairo.Region(
                cairo.RectangleInt(new_rect.x, new_rect.y, new_rect.w, new_rect.h)
            )
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
        """Cursor position along the dock's main axis in window-space.

        Returns cursor_x for horizontal docks, cursor_y for vertical.
        Negative when no cursor is present (mouse outside window).
        """
        if is_horizontal(pos=self.config.pos):
            return self.cursor_x
        return self.cursor_y

    def _main_axis_window_size(self) -> int:
        """Window extent along the dock's main axis."""
        w, h = self.get_size()
        return int(w if is_horizontal(pos=self.config.pos) else h)

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
        """Cursor in content-space along the main axis.

        Returns a large negative sentinel (-1e6) when no cursor is present,
        or the actual local coordinate (which can be negative if cursor is
        to the left of content) for smooth zoom taper on both edges.
        """
        mc = self._main_axis_cursor()
        if mc < 0:
            return -1e6  # sentinel: no cursor
        return mc - self._base_main_offset()

    def zoomed_main_offset(self, layout: list[LayoutItem]) -> float:
        """Main-axis offset to center the zoomed content in the window.

        Unlike _base_main_offset() which uses rest-state width, this uses
        the actual zoomed layout bounds so the offset matches where icons
        are rendered during hover.
        """
        left_edge, right_edge = content_bounds(
            layout=layout,
            icon_size=self.config.icon_size,
            h_padding=self.theme.h_padding,
            item_padding=self.theme.item_padding,
        )
        zoomed_w = right_edge - left_edge
        return (self._main_axis_window_size() - zoomed_w) / 2 - left_edge

    # Keep short aliases used by other modules
    def local_cursor_x(self) -> float:
        """Alias for local_cursor_main (backward compat)."""
        return self.local_cursor_main()

    def zoomed_x_offset(self, layout: list[LayoutItem]) -> float:
        """Alias for zoomed_main_offset (backward compat)."""
        return self.zoomed_main_offset(layout=layout)

    def hit_test(self, main_coord: float, layout: list[LayoutItem]) -> DockItem | None:
        """Find which DockItem is under the cursor along the main axis.

        Converts main_coord from window-space to content-space using the
        zoomed offset, then checks each layout item's [x, x+width) range.
        Returns None if cursor is in empty space between or outside items.
        """
        offset = self.zoomed_main_offset(layout=layout)
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
