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

from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk  # noqa: E402

from docking.applets.base import is_applet
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.core.position import Position, is_horizontal
from docking.i18n import _
from docking.log import get_logger
from docking.platform.barriers import PointerBarrier
from docking.platform.launcher import launch, open_target
from docking.platform.struts import clear_struts, set_dock_struts
from docking.ui import geometry
from docking.ui.autohide import HideState
from docking.ui.geometry import DockGeometryFrame, Rect, build_geometry_frame
from docking.ui.hover import HoverManager
from docking.ui.tooltip import TooltipManager

_log = get_logger(name="dock_window")

# Re-exported for existing callers/tests.
TRIGGER_PX = geometry.TRIGGER_PX
TRIGGER_PX_TOP = geometry.TRIGGER_PX_TOP
compute_input_rect = geometry.compute_input_rect

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
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
def should_keep_cursor_on_leave(autohide_enabled: bool, preview_visible: bool) -> bool:
    """Whether to preserve cursor position when mouse leaves the dock.

    True when autohide is active (smooth zoom decay during hide animation)
    or preview popup is visible (mouse moved into preview, zoom should hold).
    """
    return autohide_enabled or preview_visible


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

        self._active_display_timer: int = 0
        self._active_monitor: Gdk.Monitor | None = None
        self._barrier = PointerBarrier()
        self._screen_signal_handlers: list[tuple[object, int]] = []
        self._geometry_refresh_source: int = 0
        self._last_geometry_frame: DockGeometryFrame | None = None
        self._dock_hovered: bool = False
        self._last_autohide_state: HideState | None = None

        self._setup_window()
        self._setup_drawing_area()
        self._connect_model()

    def _setup_window(self) -> None:
        """Configure GTK window as an X11 dock.

        Sets window manager hints (DOCK type, skip taskbar/pager, keep above,
        sticky) and enables RGBA visual for composited transparency.
        """
        self.set_title(_("Docking"))
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

        DockWindow._attach_screen_signals(self, screen)
        self.connect("realize", self._on_realize)
        self.connect("screen-changed", self._on_screen_changed)
        self.connect("notify::scale-factor", self._on_scale_factor_changed)
        self.connect("destroy", self._on_destroy)
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

        # Preview/autohide policy in simple terms:
        #
        # - preview visible => dock stays visible
        # - preview hidden => dock may autohide
        # - leaving dock should schedule preview hide when preview is visible
        # - autohide should trigger when the preview actually finishes hiding,
        #   not at the first dock leave
        #
        # The preview is meant to be reachable, so once it is visible we treat
        # the dock and preview as one temporary hover region. Leaving the dock
        # does not mean the interaction is over yet; the user may be moving
        # into the preview. Because of that, dock leave only arms the preview's
        # grace timer here. If the user never reaches the preview, the preview
        # will hide shortly after and only then will autohide be released.
        preview_visible = bool(self._preview and self._preview.get_visible())
        if self._preview and preview_visible:
            self._preview.schedule_hide()

        self._update_dock_size()
        self.drawing_area.queue_draw()
        if not preview_visible:
            self.autohide.on_mouse_leave()

    def _pointer_inside_input_rect(self) -> bool:
        """Return True when pointer is inside current dock input region."""
        input_rect = self._current_input_rect()
        if input_rect is None or not self.get_realized():
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
        return input_rect.contains(x=local_x, y=local_y)

    def _get_geometry_frame(
        self,
        *,
        main_cursor: float | None = None,
        cursor_x: float | None = None,
        cursor_y: float | None = None,
    ) -> DockGeometryFrame:
        """Build a shared geometry frame for the current dock state."""
        width, height = self.get_size()
        pos = self.config.pos
        if main_cursor is not None:
            resolved_main_cursor = main_cursor
        elif cursor_x is not None and cursor_y is not None:
            main_cursor = cursor_x if is_horizontal(pos=pos) else cursor_y
            resolved_main_cursor = main_cursor
        else:
            resolved_main_cursor = self._main_axis_cursor()
        autohide_state = (
            self.autohide.state if self.autohide and self.autohide.enabled else None
        )
        zoom_progress = (
            self.autohide.zoom_progress
            if self.autohide and self.autohide.enabled
            else 1.0
        )
        hide_offset = (
            self.autohide.hide_offset
            if self.autohide and self.autohide.enabled
            else 0.0
        )
        return build_geometry_frame(
            items=self.model.visible_items(),
            config=self.config,
            theme=self.theme,
            window_w=width,
            window_h=height,
            cursor_main=(
                -1.0 if resolved_main_cursor is None else float(resolved_main_cursor)
            ),
            autohide_state=autohide_state,
            zoom_progress=zoom_progress,
            hide_offset=hide_offset,
        )

    def _point_inside_input_rect(self, x: float, y: float) -> bool:
        """Return True when the given local-window point is inside input rect."""
        input_rect = self._current_input_rect()
        if input_rect is None:
            return False
        return input_rect.contains(x=x, y=y)

    def _current_input_rect(self) -> Rect | None:
        frame = self._last_geometry_frame
        if frame is None:
            return None
        return frame.cursor_rect

    def _on_effective_enter(self) -> None:
        if self._dock_hovered:
            return
        self._dock_hovered = True
        if self.autohide:
            self.autohide.on_mouse_enter()

    def _on_effective_leave(self, widget: Gtk.DrawingArea) -> None:
        preview_visible = bool(self._preview and self._preview.get_visible())
        if self._preview and preview_visible:
            self._preview.schedule_hide()

        autohide_on = bool(self.autohide and self.autohide.enabled)
        hovered_before = (
            self._hover.hovered_item.desktop_id if self._hover.hovered_item else "-"
        )
        keep_cursor = should_keep_cursor_on_leave(
            autohide_enabled=autohide_on, preview_visible=bool(preview_visible)
        )
        self._dock_hovered = False
        if not keep_cursor:
            self._hover.hovered_item = None
            self.cursor_x = -1.0
            self.cursor_y = -1.0

        _log.debug(
            (
                "leave-policy: hovered_before=%s keep_cursor=%s "
                "preview_visible=%s autohide=%s hovered_after=%s "
                "cursor=(%.0f,%.0f)"
            ),
            hovered_before,
            keep_cursor,
            preview_visible,
            autohide_on,
            self._hover.hovered_item.desktop_id if self._hover.hovered_item else "-",
            self.cursor_x,
            self.cursor_y,
        )

        self._hover.cancel()
        self._tooltip.hide()
        self._update_dock_size()
        widget.queue_draw()
        if autohide_on and self.autohide and not preview_visible:
            self.autohide.on_mouse_leave()

    def set_preview_popup(self, preview: PreviewPopup) -> None:
        self._preview = preview
        self._preview.set_dock_window(self)
        self._hover.set_preview(preview)

    def is_pointer_inside_dock(self) -> bool:
        """Return True when the current pointer is inside the dock input area."""
        return self._pointer_inside_input_rect()

    def current_monitor_choice(self) -> int:
        """Current monitor menu selection (-1=primary, >=0 specific monitor)."""
        display = self.get_display()
        if not display:
            return -1
        n_monitors = display.get_n_monitors()
        if n_monitors <= 0:
            return -1
        selected = int(self.config.monitor_index)
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
            label = _("Display {display}: {width}x{height}").format(
                display=idx + 1,
                width=geom.width,
                height=geom.height,
            )
            if idx == primary_idx:
                label += f" ({_('Primary')})"
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
        if self.config.active_display and self._active_monitor is not None:
            return self._active_monitor

        get_n = getattr(display, "get_n_monitors", None)
        if not callable(get_n):
            return display.get_primary_monitor() or display.get_monitor(0)

        n_monitors = get_n()
        if n_monitors <= 0:
            return None

        selected = int(self.config.monitor_index)
        if 0 <= selected < n_monitors:
            monitor = display.get_monitor(selected)
            if monitor is not None:
                return monitor

        return display.get_primary_monitor() or display.get_monitor(0)

    def _on_realize(self, _widget: Gtk.Widget) -> None:
        """Position dock and set struts after window is realized."""
        DockWindow._attach_screen_signals(self, self.get_screen())
        display = self.get_display()
        if display and isinstance(display, GdkX11.X11Display):
            self._barrier.initialize(gdk_display=display)
        self._position_dock()
        self._set_struts()
        self._update_input_region()
        if self.config.active_display:
            self.start_active_display()

    def _attach_screen_signals(self, screen: Gdk.Screen | None) -> None:
        DockWindow._disconnect_screen_signals(self)
        if screen is None:
            return
        connect = getattr(screen, "connect", None)
        if not callable(connect):
            return
        self._screen_signal_handlers = [
            (screen, connect("monitors-changed", self._on_screen_metrics_changed)),
            (screen, connect("size-changed", self._on_screen_metrics_changed)),
        ]

    def _disconnect_screen_signals(self) -> None:
        for obj, handler_id in self._screen_signal_handlers:
            disconnect = getattr(obj, "disconnect", None)
            if callable(disconnect):
                disconnect(handler_id)
        self._screen_signal_handlers = []

    def _on_screen_changed(
        self, _widget: Gtk.Widget, _previous_screen: Gdk.Screen | None
    ) -> None:
        DockWindow._attach_screen_signals(self, self.get_screen())
        DockWindow._schedule_reposition(self)

    def _on_screen_metrics_changed(self, *_args: object) -> None:
        DockWindow._schedule_reposition(self)

    def _on_scale_factor_changed(self, *_args: object) -> None:
        DockWindow._schedule_reposition(self)

    def _schedule_reposition(self) -> None:
        if not self.get_realized():
            return
        if self._geometry_refresh_source:
            return
        self._geometry_refresh_source = GLib.idle_add(
            DockWindow._apply_scheduled_reposition, self
        )

    def _apply_scheduled_reposition(self) -> bool:
        self._geometry_refresh_source = 0
        self.reposition()
        return False

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        refresh_source = self._geometry_refresh_source
        if refresh_source:
            GLib.source_remove(refresh_source)
            self._geometry_refresh_source = 0
        DockWindow._disconnect_screen_signals(self)

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
        gap = max(0, int(self.theme.distance_from_edge))
        # Window includes the gap in its cross-axis size so it still
        # touches the screen edge. This lets the autohide trigger strip
        # catch the mouse at the physical edge. The renderer draws dock
        # content offset by the gap, keeping the visual floating effect.
        if is_horizontal(pos=pos):
            win_w, win_h = geom.width, cross + gap
            if pos == Position.BOTTOM:
                win_x = geom.x
                win_y = geom.y + geom.height - win_h
            else:  # TOP
                win_x = geom.x
                win_y = workarea.y
        else:
            win_w, win_h = cross + gap, workarea.height
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

        self._update_barrier()

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
        gap = max(0, int(self.theme.distance_from_edge))
        strut_height = int(icon_size + self.theme.bottom_padding + gap)

        set_dock_struts(
            gdk_window=gdk_window,
            dock_height=strut_height,
            monitor_geom=geom,
            screen=screen,
            position=self.config.pos,
        )

    def _update_barrier(self) -> None:
        """Create or destroy the pointer barrier based on autohide state."""
        if not self._barrier.supported:
            return
        if not self.config.autohide:
            self._barrier.destroy()
            return
        display = self.get_display()
        monitor = DockWindow._resolve_target_monitor(self, display=display)
        if monitor is None:
            self._barrier.destroy()
            return
        geom = monitor.get_geometry()
        self._barrier.update(
            position=self.config.pos,
            monitor_x=geom.x,
            monitor_y=geom.y,
            monitor_w=geom.width,
            monitor_h=geom.height,
        )

    def _clear_struts(self) -> None:
        """Remove strut reservation by setting all struts to zero."""
        gdk_window = self.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return
        clear_struts(gdk_window=gdk_window)

    def update_struts(self) -> None:
        """Public method to refresh struts and barrier after autohide toggle.

        Called from the menu when the user switches between autohide
        and always-visible modes. Immediately updates the X11 strut
        reservation so the window manager resizes application windows,
        and creates/destroys the pointer barrier accordingly.
        """
        self._set_struts()
        self._update_barrier()

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
        current_autohide_state = None
        if self.autohide and self.autohide.enabled:
            current_autohide_state = self.autohide.state
            _log.debug(
                (
                    "draw: state=%s hide_offset=%.3f zoom_progress=%.3f "
                    "hovered=%s cursor=(%.0f,%.0f)"
                ),
                self.autohide.state.value,
                hide_offset,
                zoom_progress,
                hovered_id or "-",
                self.cursor_x,
                self.cursor_y,
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

        # Reset cursor/hover after hide completes
        if self.autohide and self.autohide.state == HideState.HIDDEN:
            self.cursor_x = -1.0
            self.cursor_y = -1.0
            self._hover.hovered_item = None
            self._dock_hovered = False
            self._tooltip.hide()
        elif (
            self._last_autohide_state == HideState.SHOWING
            and current_autohide_state == HideState.VISIBLE
            and self._dock_hovered
            and self._hover.hovered_item is not None
        ):
            frame = self._get_geometry_frame()
            self._hover.update(self._main_axis_cursor(), frame=frame)

        # Keep redraw pump alive while urgent glow is visible (dock hidden)
        if self._has_active_urgent_glow():
            GLib.timeout_add(16, self._urgent_glow_tick)

        self._last_autohide_state = current_autohide_state

        return True

    def _on_motion(self, widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        """Update cursor position, trigger zoom redraw, and refresh hover state.

        Returns False to propagate the event so GTK's drag source can
        detect the drag threshold and initiate DnD when appropriate.
        """
        self.cursor_x = event.x
        self.cursor_y = event.y
        frame = self._get_geometry_frame()
        self._update_dock_size(frame=frame)
        widget.queue_draw()
        if frame.cursor_rect.contains(event.x, event.y):
            self._on_effective_enter()
            self._hover.update(self._main_axis_cursor(), frame=frame)
        elif self._dock_hovered:
            self._on_effective_leave(widget)
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
            frame = self._get_geometry_frame(cursor_x=event.x, cursor_y=event.y)
            item = frame.item_at_point(event.x, event.y)
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
                    self._tooltip.update(item, frame)
                self._hover.start_anim_pump(350)
                return True

            if item.kind == FOLDER_KIND:
                if self._menu:
                    self._menu.show_item(event, item)
                self._hover.start_anim_pump(350)
                return True

            if item.kind == FILE_KIND:
                item.last_launched = now
                open_target(item.target)
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
        frame = self._get_geometry_frame(cursor_x=event.x, cursor_y=event.y)
        item = frame.item_at_point(event.x, event.y)
        if item and is_applet(desktop_id=item.desktop_id):
            applet = self.model.get_applet(item.desktop_id)
            if applet:
                direction_up = event.direction == Gdk.ScrollDirection.UP
                applet.on_scroll(direction_up)
                # Refresh tooltip immediately (item.name may have changed)
                self._tooltip.update(item, frame)
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
        # inside the dock's current cursor region. Ignore those leaves
        # using the same half-open region semantics as the rest of the
        # shared geometry layer.
        input_rect = self._current_input_rect()
        if input_rect is not None:
            if input_rect.contains(event.x, event.y):
                return False

        if not self._dock_hovered:
            return False

        # Preview/autohide policy in simple terms:
        #
        # - preview visible => dock stays visible
        # - preview hidden => dock may autohide
        # - leaving dock should schedule preview hide when preview is visible
        # - autohide should trigger when the preview actually finishes hiding,
        #   not at the first dock leave
        #
        # This keeps the preview reachable. A user moving from the dock toward
        # the preview should not make the dock immediately hide underneath the
        # interaction. When a preview is visible, dock leave only schedules the
        # preview to disappear after its grace period; the preview layer decides
        # whether autohide should proceed once that timer actually completes.
        self._on_effective_leave(widget)
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
        self._on_effective_enter()
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

    def _update_dock_size(self, frame: DockGeometryFrame | None = None) -> None:
        """Refresh the input region after model or cursor changes.

        The window itself doesn't resize (it spans the full monitor),
        but the clickable input region needs to track the content bounds.
        """
        self._update_input_region(frame=frame)

    def _update_input_region(self, frame: DockGeometryFrame | None = None) -> None:
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

        The concrete region now comes from the shared DockGeometryFrame. That
        keeps input masking, hover hit-testing, popup anchoring, and pointer
        containment on the same geometry source instead of rebuilding window
        interaction bounds separately inside DockWindow.
        """
        gdk_window = self.get_window()
        if not gdk_window:
            return
        frame = frame or self._get_geometry_frame()
        old_rect = self._current_input_rect()
        self._last_geometry_frame = frame
        new_rect = frame.cursor_rect
        if new_rect != old_rect:
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

    def start_active_display(self) -> None:
        """Start polling cursor position for active display tracking."""
        if self._active_display_timer:
            return
        self._active_display_timer = GLib.timeout_add_seconds(
            2, self._poll_active_display
        )

    def stop_active_display(self) -> None:
        """Stop active display polling."""
        if self._active_display_timer:
            GLib.source_remove(self._active_display_timer)
            self._active_display_timer = 0

    def _poll_active_display(self) -> bool:
        """Poll cursor position and move dock to the monitor under cursor."""
        display = self.get_display()
        if not display:
            return True
        seat = display.get_default_seat()
        if not seat:
            return True
        pointer = seat.get_pointer()
        if not pointer:
            return True
        _, x, y = pointer.get_position()
        monitor = display.get_monitor_at_point(x, y)
        if monitor is not None and monitor != self._active_monitor:
            self._active_monitor = monitor
            self.reposition()
        return True

    def reposition(self) -> None:
        """Re-layout after position change -- reposition window, struts, input."""
        self._position_dock()
        self._set_struts()
        self._update_input_region()
        self.drawing_area.queue_draw()

    def queue_redraw(self) -> None:
        """Convenience for external controllers to trigger redraw."""
        self.drawing_area.queue_draw()
