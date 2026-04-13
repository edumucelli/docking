"""GTK dock window shell that binds together rendering, geometry, and policy.

What this class is

`DockWindow` is the real GTK/X11 window that the user sees and interacts with.
It is not "the dock logic" in the abstract. It is the shell that:

- owns the GTK window and drawing area,
- receives raw pointer/button/scroll/crossing events,
- owns long-lived UI collaborators,
- turns raw events into calls to the right subsystem.

It is intentionally the composition root of the runtime UI layer.

What this class is not

`DockWindow` is no longer meant to directly implement every dock concern.
That decomposition matters because this file used to become the place where
every cross-feature fix landed. The current split is:

- DockWindow
  GTK shell and event adapter

- DockPlacementController
  monitor choice, reposition, struts, barriers, active-display polling

- DockInteractionCoordinator
  effective enter/leave, menu-open policy, preview-aware leave policy

- DockGeometryBuilder
  window state -> shared geometry frame

- HoverManager / TooltipManager / PreviewPopup / DnDHandler / MenuHandler
  focused feature owners

The point of this file is to connect those pieces, not to replace them.

What kind of window this is

This is not a normal application window. It is created as a dock/panel window:

- `WindowTypeHint.DOCK`
- sticky across workspaces
- keep-above
- edge-positioned

In always-visible mode it may also publish struts so the window manager reserves
space for it. In autohide mode it still exists at the edge, but only a thin
trigger strip may remain interactive while hidden.

Visual window vs interactive dock

The GTK window is not the same thing as the active dock band.

Why:
- the window wants to stay stable and edge-aligned,
- zooming icons should not force the whole top-level window to wobble,
- transparent space should pass clicks through,
- hidden autohide state should still leave a tiny trigger at the edge.

Visually:

    GTK window
    +-------------------------------------------------------------+
    |                                                             |
    |   active dock band / cursor_rect                            |
    |   +-----------------------------------------------+         |
    |   | icons / shelf / hover / popup anchors         |         |
    |   +-----------------------------------------------+         |
    |                                                             |
    +-------------------------------------------------------------+

Only the active band should intercept pointer input. The rest of the window is
structural space used to keep animation and rendering stable.

That is why DockWindow owns the X11 input shape update path while geometry owns
the actual rectangle math.

Main runtime data flow

The most important runtime cycle is:

    raw GTK event
        |
        v
    DockWindow updates cursor / button context
        |
        v
    DockWindow asks geometry builder for current frame
        |
        +--> interaction policy consumes frame
        +--> hover consumes frame
        +--> renderer consumes frame
        +--> input mask may be refreshed from frame
        +--> menus / drag/drop may target items from frame

This "one shared frame" model is the main reason the dock now behaves more
consistently than before the geometry refactor.

Draw path responsibilities

Draw does more than paint pixels. A draw/update cycle is also where the dock:

1. builds the current frame,
2. gives the frame to the renderer,
3. updates the active input region if needed,
4. performs any post-transition reconciliation tied to autohide state,
5. schedules another redraw only when animation or visual effects require it.

That may sound unusual, but it is deliberate. Rendering, input shape, and
autohide state all depend on the same live geometry and must stay aligned.

Event routing responsibilities

DockWindow receives these raw categories:

- motion
- enter / leave
- button press / release
- scroll
- draw

And routes them approximately like this:

    motion
      |
      +--> geometry.build_frame(...)
      +--> interaction enter/leave reconciliation
      +--> hover.update(...)
      +--> redraw if visuals changed

    button press/release
      |
      +--> geometry target lookup
      +--> launcher / item action / applet action / menu

    scroll
      |
      +--> applet scroll or dock-level behavior

    draw
      |
      +--> renderer.draw(frame)
      +--> update_input_region(frame)

This file should stay focused on that routing. If a new block of logic starts
describing placement policy, hover policy, or menu policy in detail, it likely
belongs elsewhere.

Autohide and cursor preservation

DockWindow participates in autohide, but it does not own the autohide state
machine. Its job is to supply the controller with correct higher-level facts:

- pointer effectively entered,
- pointer effectively left,
- a menu or drag temporarily disables hiding,
- draw/update must reflect the current hide offset.

The dock also intentionally preserves cursor/hover identity across some leave
transitions so that icons do not snap before the hide animation carries them
away. That policy is coordinated with `DockInteractionCoordinator`.

Why raw leave events are not enough

The GTK window boundary is not the same as the dock boundary. So a raw leave is
only a candidate signal. The dock must ask:

    "Did the pointer really leave the current input region?"

That question is answered with the shared geometry frame, not with the widget
size alone. This is one of the main architectural lessons encoded in the
current codebase.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk

from docking.applets.identity import is_applet_desktop_id as is_applet
from docking.core.config import LeftClickAction, MiddleClickAction
from docking.core.items import FILE_KIND, FOLDER_KIND
from docking.core.position import Position, is_horizontal
from docking.i18n import _
from docking.log import get_logger
from docking.platform.environment import detect_desktop, log_runtime_snapshot
from docking.platform.launcher import launch, launch_new_window, open_target
from docking.platform.struts import (
    BlurRect,
    clear_blur_region,
    compute_blur_region,
    set_blur_region,
)
from docking.ui import geometry
from docking.ui.about import AboutDialogController
from docking.ui.autohide import AutoHideController, HideState
from docking.ui.dnd import DnDHandler
from docking.ui.effects import ZoomAnimator
from docking.ui.geometry import (
    DockGeometryBuilder,
    DockGeometryFrame,
    Rect,
    current_input_rect,
)
from docking.ui.hover import HoverManager
from docking.ui.interaction import DockInteractionCoordinator
from docking.ui.menu import MenuHandler
from docking.ui.placement import DockPlacementController
from docking.ui.preview import PreviewPopup
from docking.ui.runtime import DockRuntime
from docking.ui.settings import SettingsWindowController
from docking.ui.tooltip import TooltipManager

log = get_logger(name="dock_window")

# Re-exported for existing callers/tests.
TRIGGER_PX = geometry.TRIGGER_PX
TRIGGER_PX_TOP = geometry.TRIGGER_PX_TOP
compute_input_rect = geometry.compute_input_rect

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.theme import Theme
    from docking.platform.dodge import WindowDodgeMonitor
    from docking.platform.launcher import Launcher
    from docking.platform.model import DockModel
    from docking.platform.window_tracker import WindowTracker
    from docking.ui.renderer import DockRenderer


@dataclass
class _PerfCounters:
    enabled: bool = False
    start_monotonic: float = 0.0
    motion_events: int = 0
    draw_callbacks: int = 0
    redraw_requests: int = 0
    redraw_request_tick_animation: int = 0
    redraw_request_motion: int = 0
    redraw_request_model_change: int = 0
    redraw_request_queue_redraw: int = 0
    redraw_request_urgent_glow: int = 0
    update_input_region_calls: int = 0
    input_region_native_writes: int = 0
    blur_sync_calls: int = 0
    blur_region_native_writes: int = 0
    blur_region_clears: int = 0
    geometry_frame_cache_hits: int = 0
    geometry_frame_cache_misses: int = 0

    @classmethod
    def create(cls) -> _PerfCounters:
        enabled = os.environ.get("DOCKING_PERF_STATS", "").strip() == "1"
        return cls(
            enabled=enabled,
            start_monotonic=time.monotonic() if enabled else 0.0,
        )

    def bump(self, field: str, count: int = 1) -> None:
        if not self.enabled:
            return
        setattr(self, field, getattr(self, field) + count)

    def log_summary(self) -> None:
        if not self.enabled:
            return
        elapsed = max(time.monotonic() - self.start_monotonic, 0.001)
        log.info(
            (
                "perf summary: elapsed_s=%.3f motion_events=%d draw_callbacks=%d "
                "redraw_requests=%d redraw_tick_animation=%d redraw_motion=%d "
                "redraw_model_change=%d redraw_queue_redraw=%d "
                "redraw_urgent_glow=%d update_input_region_calls=%d "
                "input_region_native_writes=%d blur_sync_calls=%d "
                "blur_region_native_writes=%d blur_region_clears=%d "
                "geometry_frame_cache_hits=%d geometry_frame_cache_misses=%d "
                "motion_hz=%.1f draw_hz=%.1f redraw_hz=%.1f"
            ),
            elapsed,
            self.motion_events,
            self.draw_callbacks,
            self.redraw_requests,
            self.redraw_request_tick_animation,
            self.redraw_request_motion,
            self.redraw_request_model_change,
            self.redraw_request_queue_redraw,
            self.redraw_request_urgent_glow,
            self.update_input_region_calls,
            self.input_region_native_writes,
            self.blur_sync_calls,
            self.blur_region_native_writes,
            self.blur_region_clears,
            self.geometry_frame_cache_hits,
            self.geometry_frame_cache_misses,
            self.motion_events / elapsed,
            self.draw_callbacks / elapsed,
            self.redraw_requests / elapsed,
        )


@dataclass
class _GeometryFrameCacheEntry:
    """Cached geometry frame plus the signature that validates reuse."""

    frame: DockGeometryFrame
    signature: tuple[object, ...]

    def matches(self, *, signature: tuple[object, ...]) -> bool:
        return self.signature == signature


@dataclass
class _DockWindowCache:
    """DockWindow-owned cached artifacts derived from current window state.

    This groups the reusable geometry/input/blur state that used to live as
    separate private attributes on DockWindow. It deliberately does not own
    higher-level interaction state such as hover/autohide/menu coordination.
    """

    geometry_frame: _GeometryFrameCacheEntry | None = None
    applied_input_frame: DockGeometryFrame | None = None
    last_blur_region: tuple[int, ...] | None = None

    @classmethod
    def create(cls) -> _DockWindowCache:
        return cls()

    def invalidate_geometry_frame(self) -> None:
        self.geometry_frame = None

    def store_geometry_frame(
        self,
        *,
        frame: DockGeometryFrame,
        signature: tuple[object, ...],
    ) -> DockGeometryFrame:
        self.geometry_frame = _GeometryFrameCacheEntry(
            frame=frame,
            signature=signature,
        )
        return frame

    def matching_geometry_frame(
        self,
        *,
        signature: tuple[object, ...],
    ) -> DockGeometryFrame | None:
        if self.geometry_frame is None:
            return None
        if not self.geometry_frame.matches(signature=signature):
            return None
        return self.geometry_frame.frame


# Minimum pixel movement between press and release to consider it a drag
# rather than a click. Prevents accidental launches when slightly moving
# the mouse during a click.
CLICK_DRAG_THRESHOLD = 10
REDRAW_FRAME_INTERVAL_MS = 16
SHORT_ANIMATION_PUMP_MS = 350
BOUNCE_ANIMATION_PUMP_MS = 700


# Thin input region at screen edge when dock is hidden, allowing the
# mouse to re-enter and trigger the show animation.
# X11 mouse button codes
MOUSE_LEFT = 1
MOUSE_MIDDLE = 2
MOUSE_RIGHT = 3
def hover_anchor_from_draw_rect(
    *, win_x: int, win_y: int, draw_rect: Rect, position: Position
) -> tuple[int, int]:
    """Translate an item draw rect into the preview/hover anchor point."""
    if position == Position.BOTTOM:
        return int(win_x + draw_rect.x), int(win_y + draw_rect.y)
    if position == Position.TOP:
        return int(win_x + draw_rect.x), int(win_y + draw_rect.y + draw_rect.h)
    if position == Position.LEFT:
        return int(win_x + draw_rect.x + draw_rect.w), int(win_y + draw_rect.y)
    return int(win_x + draw_rect.x), int(win_y + draw_rect.y)


class DockWindow(Gtk.Window):
    """Top-level dock window that owns event routing and edge integration.

    DockWindow acts as the composition root for runtime UI behavior: it keeps
    references to model, renderer, hover manager, tooltip manager, preview,
    dnd, menu, and autohide controller, then coordinates them from
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
        launcher: Launcher,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.config = config
        self.model = model
        self.renderer = renderer
        self.theme = theme
        self.window_tracker = window_tracker
        self.cursor_x: float = -1.0
        self.cursor_y: float = -1.0
        self.autohide: AutoHideController
        self.dnd: DnDHandler
        self._menu: MenuHandler
        self.preview: PreviewPopup
        self._menu_popup_visible: bool = False
        self.tooltip = TooltipManager(self, config, model, theme)
        self.geometry = DockGeometryBuilder(self)

        self.hover = HoverManager(
            self,
            config,
            model,
            theme,
            self.tooltip,
            geometry_builder=self.geometry,
        )

        self.placement = DockPlacementController(self)
        self.interaction = DockInteractionCoordinator(self)
        self._cache = _DockWindowCache.create()
        self._redraw_source_id: int | None = None
        self._perf = _PerfCounters.create()
        self.dock_hovered: bool = False
        self._last_autohide_state: HideState | None = None
        self.dodge_monitor: WindowDodgeMonitor | None = None

        self._setup_window()
        self._setup_drawing_area()
        self.zoom_animator = ZoomAnimator(self.drawing_area)
        self._build_components(launcher=launcher)
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
        log_runtime_snapshot(display=self.get_display(), desktop=detect_desktop())

        self.placement.attach_screen_signals(screen)
        self.connect("realize", self.placement.on_realize)
        self.connect("screen-changed", self.placement.on_screen_changed)
        self.connect("notify::scale-factor", self.placement.on_scale_factor_changed)
        self.connect("destroy", self.placement.on_destroy)
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
        self.model.add_change_listener(self._on_model_changed)

    def _disconnect_model(self) -> None:
        """Remove model listener during shutdown."""
        self.model.remove_change_listener(self._on_model_changed)

    def _on_destroy(self, _window: Gtk.Window) -> None:
        """Release model subscriptions owned by the dock shell."""
        perf = getattr(self, "_perf", None)
        if perf is not None:
            perf.log_summary()
        renderer = getattr(self, "renderer", None)
        if perf is not None and perf.enabled and renderer is not None:
            renderer_stats = renderer.cache_stats()
            geometry_hits = perf.geometry_frame_cache_hits
            geometry_misses = perf.geometry_frame_cache_misses
            geometry_total = geometry_hits + geometry_misses
            icon_hits = renderer_stats["icon_surface_hits"]
            icon_misses = renderer_stats["icon_surface_misses"]
            icon_total = icon_hits + icon_misses
            offscreen_hits = renderer_stats["offscreen_hits"]
            offscreen_misses = renderer_stats["offscreen_misses"]
            offscreen_total = offscreen_hits + offscreen_misses
            log.info(
                (
                    "cache summary: geometry_frame=%d/%d (%.1f%%) "
                    "icon_surface=%d/%d (%.1f%%) offscreen=%d/%d (%.1f%%)"
                ),
                geometry_hits,
                geometry_total,
                (100.0 * geometry_hits / geometry_total) if geometry_total else 0.0,
                icon_hits,
                icon_total,
                (100.0 * icon_hits / icon_total) if icon_total else 0.0,
                offscreen_hits,
                offscreen_total,
                (100.0 * offscreen_hits / offscreen_total) if offscreen_total else 0.0,
            )
        self._disconnect_model()

    def _build_components(self, *, launcher: Launcher) -> None:
        """Build long-lived UI collaborators that depend on the live window."""
        runtime = DockRuntime(self)
        about = AboutDialogController(parent=self)
        settings = SettingsWindowController(
            parent=self,
            runtime=runtime,
            model=self.model,
            config=self.config,
        )
        self.autohide = AutoHideController(self, self.config)
        self.dnd = DnDHandler(
            drawing_area=self.drawing_area,
            window=self,
            model=self.model,
            config=self.config,
            renderer=self.renderer,
            theme=self.theme,
            launcher=launcher,
            geometry_builder=self.geometry,
        )
        self._menu = MenuHandler(
            about=about,
            settings=settings,
            runtime=runtime,
            model=self.model,
            config=self.config,
            window_tracker=self.window_tracker,
            geometry_builder=self.geometry,
            launcher=launcher,
        )
        self.preview = PreviewPopup(window_tracker=self.window_tracker)
        self.preview.set_pointer_inside_dock_probe(self.is_pointer_inside_dock)
        self.preview.set_autohide(controller=self.autohide)
        self.hover.set_preview(preview=self.preview)

    def is_pointer_inside_dock(self) -> bool:
        """Return True when the current pointer is inside the dock input area."""
        return self.interaction.is_pointer_inside_dock()

    def current_interaction_frame(self) -> DockGeometryFrame | None:
        """Return the best available frame for pointer/input containment checks."""
        entry = self._cache.geometry_frame
        return (
            entry.frame if entry is not None else None
        ) or self._cache.applied_input_frame

    def _bump_perf(self, field: str, count: int = 1) -> None:
        perf = getattr(self, "_perf", None)
        if perf is None:
            return
        perf.bump(field, count)

    def _invalidate_current_geometry_frame(self) -> None:
        self._cache.invalidate_geometry_frame()

    def _clear_scheduled_redraw(self) -> None:
        self._redraw_source_id = None

    def _flush_scheduled_redraw(self) -> bool:
        self._redraw_source_id = None
        self.drawing_area.queue_draw()
        return False

    def _schedule_redraw(self) -> None:
        if self._redraw_source_id is not None:
            return
        self._redraw_source_id = GLib.timeout_add(
            REDRAW_FRAME_INTERVAL_MS,
            DockWindow._flush_scheduled_redraw,
            self,
        )

    def _geometry_signature(
        self,
        *,
        drop_insert_index: int = -1,
        cursor_x: float | None = None,
        cursor_y: float | None = None,
    ) -> tuple[object, ...]:
        size_getter = getattr(self, "get_size", None)
        if callable(size_getter):
            window_w, window_h = size_getter()
        else:
            window_w, window_h = None, None
        resolved_x = getattr(self, "cursor_x", None) if cursor_x is None else cursor_x
        resolved_y = getattr(self, "cursor_y", None) if cursor_y is None else cursor_y
        autohide = getattr(self, "autohide", None)
        autohide_enabled = bool(getattr(autohide, "enabled", False))
        autohide_state = getattr(autohide, "state", None) if autohide_enabled else None
        autohide_zoom = (
            getattr(autohide, "zoom_progress", 1.0) if autohide_enabled else 1.0
        )
        zoom_animator = getattr(self, "zoom_animator", None)
        zoom_progress = getattr(zoom_animator, "progress", 1.0) * autohide_zoom
        hide_offset = getattr(autohide, "hide_offset", 0.0) if autohide_enabled else 0.0
        return (
            window_w,
            window_h,
            resolved_x,
            resolved_y,
            drop_insert_index,
            autohide_state,
            zoom_progress,
            hide_offset,
        )

    def _build_and_store_geometry_frame(
        self,
        *,
        drop_insert_index: int = -1,
        cursor_x: float | None = None,
        cursor_y: float | None = None,
    ) -> DockGeometryFrame:
        frame = self.geometry.build_frame(
            drop_insert_index=drop_insert_index,
            cursor_x=cursor_x,
            cursor_y=cursor_y,
        )
        return self._cache.store_geometry_frame(
            frame=frame,
            signature=DockWindow._geometry_signature(
                self,
                drop_insert_index=drop_insert_index,
                cursor_x=cursor_x,
                cursor_y=cursor_y,
            ),
        )

    def _current_or_build_geometry_frame(
        self,
        *,
        drop_insert_index: int = -1,
    ) -> DockGeometryFrame:
        expected_signature = DockWindow._geometry_signature(
            self,
            drop_insert_index=drop_insert_index,
        )
        current_frame = self._cache.matching_geometry_frame(
            signature=expected_signature
        )
        if current_frame is not None:
            DockWindow._bump_perf(self, "geometry_frame_cache_hits")
            return current_frame
        DockWindow._bump_perf(self, "geometry_frame_cache_misses")
        return DockWindow._build_and_store_geometry_frame(
            self,
            drop_insert_index=drop_insert_index,
        )

    def get_hover_anchor(self, *, desktop_id: str) -> tuple[int, int, str] | None:
        """Return the absolute hover/preview anchor for one visible item."""
        if not self.get_realized():
            return None
        item = self.model.find_by_desktop_id(desktop_id=desktop_id)
        if item is None:
            return None
        frame = self.geometry.build_frame()
        geometry = frame.geometry_for_item(item)
        if geometry is None:
            return None
        win_x, win_y = self.get_position()
        x, y = hover_anchor_from_draw_rect(
            win_x=win_x,
            win_y=win_y,
            draw_rect=geometry.draw_rect,
            position=self.config.pos,
        )
        return x, y, self.config.pos.value

    def on_hide_mode_changed(self) -> None:
        """Reconcile live autohide/dodge state after hide-mode changes."""
        if not self.autohide.enabled:
            self.autohide.reset()
        else:
            self.autohide.set_hovered(self.is_pointer_inside_dock())
            self.autohide.reconcile()

        self.placement.update_struts()
        self.update_input_region()
        self.queue_redraw()

        if self.dodge_monitor is not None:
            self.dodge_monitor.evaluate_now()

    def _on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        """GTK draw signal handler -- orchestrates each frame.

        Delegates rendering to the DockRenderer, then updates the input
        region (which may change during hide animation), resets cursor
        after hide completes, and re-schedules redraws for urgent glow.
        """
        DockWindow._clear_scheduled_redraw(self)
        DockWindow._bump_perf(self, "draw_callbacks")
        hide_offset = self.autohide.hide_offset
        # zoom_progress for debug logging only -- the geometry layer
        # composes hover zoom * autohide zoom in capture_geometry_inputs().
        autohide_zoom = self.autohide.zoom_progress if self.autohide.enabled else 1.0
        zoom_progress = self.zoom_animator.progress * autohide_zoom
        drag_index = self.dnd.drag_index
        drop_insert = self.dnd.drop_insert_index
        drop_target = self.dnd.drop_target_id
        hovered_id = (
            self.hover.hovered_item.desktop_id
            if self.hover and self.hover.hovered_item
            else ""
        )
        current_autohide_state = None
        if self.autohide.enabled:
            current_autohide_state = self.autohide.state
            log.debug(
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
        # Advance insert/remove animations; request another draw if active
        if self.model.tick_animations():
            DockWindow._bump_perf(self, "redraw_requests")
            DockWindow._bump_perf(self, "redraw_request_tick_animation")
            DockWindow._schedule_redraw(self)

        frame = DockWindow._current_or_build_geometry_frame(
            self,
            drop_insert_index=drop_insert,
        )
        if current_autohide_state is not None:
            item_positions = [
                (
                    f"{geometry.item.desktop_id}@"
                    f"({geometry.draw_rect.x},{geometry.draw_rect.y},"
                    f"{geometry.draw_rect.w}x{geometry.draw_rect.h})"
                )
                for geometry in frame.item_geometries
            ]
            log.debug("draw items: %s", " | ".join(item_positions) or "<none>")
        self._sync_background_blur_hint(frame=frame)
        self.renderer.draw(
            cr,
            widget,
            frame,
            self.config,
            self.theme,
            hide_offset,
            drag_index,
            drop_insert,
            hovered_id,
            drop_target_id=drop_target,
        )
        # Update input region as hide state changes (shrink when hidden)
        self.update_input_region(frame=frame)

        # Reset cursor/hover after hide completes
        if self.autohide.state == HideState.HIDDEN:
            self.cursor_x = -1.0
            self.cursor_y = -1.0
            self.hover.hovered_item = None
            self.dock_hovered = False
            self.tooltip.hide()
        elif (
            self._last_autohide_state == HideState.SHOWING
            and current_autohide_state == HideState.VISIBLE
            and self.dock_hovered
            and self.hover.hovered_item is not None
        ):
            cursor_main = (
                self.cursor_x if is_horizontal(pos=self.config.pos) else self.cursor_y
            )
            self.hover.update(cursor_main, frame=frame)

        # Keep redraw pump alive while urgent glow is visible (dock hidden)
        if self.renderer.has_active_urgent_glow(
            model=self.model,
            theme=self.theme,
            autohide_state=current_autohide_state,
            now_us=GLib.get_monotonic_time(),
        ):
            DockWindow._bump_perf(self, "redraw_requests")
            DockWindow._bump_perf(self, "redraw_request_urgent_glow")
            DockWindow._schedule_redraw(self)

        self._last_autohide_state = current_autohide_state

        return True

    def _on_motion(self, widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        """Update cursor position, trigger zoom redraw, and refresh hover state.

        Returns False to propagate the event so GTK's drag source can
        detect the drag threshold and initiate DnD when appropriate.
        """
        DockWindow._bump_perf(self, "motion_events")
        self.cursor_x = event.x
        self.cursor_y = event.y
        frame = DockWindow._build_and_store_geometry_frame(self)
        self.update_input_region(frame=frame)
        DockWindow._bump_perf(self, "redraw_requests")
        DockWindow._bump_perf(self, "redraw_request_motion")
        DockWindow._schedule_redraw(self)
        stack_item_id = self._menu.open_folder_stack_item_id()
        hovered_item = frame.item_at_point(event.x, event.y)
        if stack_item_id is not None and (
            hovered_item is None or hovered_item.desktop_id != stack_item_id
        ):
            self._menu.close_folder_stack()
        if frame.cursor_rect.contains(event.x, event.y):
            self.interaction.on_effective_enter()
            cursor_main = (
                self.cursor_x if is_horizontal(pos=self.config.pos) else self.cursor_y
            )
            self.hover.update(cursor_main, frame=frame)
        elif self.dock_hovered:
            self.interaction.on_effective_leave(widget)
        return False  # Propagate so GTK drag source can detect drag threshold

    def close_open_folder_stack_for_item(self, desktop_id: str) -> None:
        """Close the folder stack if it currently belongs to the given item."""
        if self._menu.open_folder_stack_item_id() == desktop_id:
            self._menu.close_folder_stack()

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
            cursor_main = event.x if is_horizontal(pos=self.config.pos) else event.y
            self._menu.show(event, cursor_main)
            return True

        if event.button in (MOUSE_LEFT, MOUSE_MIDDLE):
            frame = DockWindow._build_and_store_geometry_frame(
                self,
                cursor_x=event.x,
                cursor_y=event.y,
            )
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
                    self.tooltip.update(item, frame)
                    self.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
                    return True

            if item.kind == FOLDER_KIND:
                item_geometry = frame.geometry_for_item(item)
                if item_geometry is not None:
                    win_x, win_y = self.get_position()
                    anchor_x, anchor_y = hover_anchor_from_draw_rect(
                        win_x=win_x,
                        win_y=win_y,
                        draw_rect=item_geometry.draw_rect,
                        position=self.config.pos,
                    )
                    icon_w = int(item_geometry.draw_rect.w)
                else:
                    win_x, win_y = self.get_position()
                    anchor_x = win_x + int(event.x)
                    anchor_y = win_y + int(event.y)
                    icon_w = int(self.config.icon_size)
                self._menu.show_folder_stack(
                    item=item,
                    anchor_x=anchor_x,
                    anchor_y=anchor_y,
                    icon_w=icon_w,
                    position=self.config.pos,
                )
                self.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
                return True

            if item.kind == FILE_KIND:
                item.last_launched = now
                open_target(item.target)
                self.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
                return True

            action = (
                self.config.middle_click_action
                if event.button == MOUSE_MIDDLE
                else self.config.left_click_action
            )
            if event.state & Gdk.ModifierType.CONTROL_MASK:
                action = MiddleClickAction.NEW_WINDOW.value

            if action == MiddleClickAction.NEW_WINDOW.value or not item.is_running:
                item.last_launched = now
                if action == MiddleClickAction.NEW_WINDOW.value:
                    launch_new_window(desktop_id=item.desktop_id)
                else:
                    launch(desktop_id=item.desktop_id)
                self.hover.start_anim_pump(BOUNCE_ANIMATION_PUMP_MS)
            elif action == LeftClickAction.CYCLE.value:
                self.window_tracker.cycle_windows(item.desktop_id)
                self.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
            elif action == MiddleClickAction.MINIMIZE.value:
                self.window_tracker.minimize_windows(item.desktop_id)
                self.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
            elif action == MiddleClickAction.CLOSE_FOCUSED.value:
                self.window_tracker.close_focused(item.desktop_id)
                self.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)
            else:
                self.window_tracker.toggle_focus(item.desktop_id)
                self.hover.start_anim_pump(SHORT_ANIMATION_PUMP_MS)

        return True

    def _on_scroll(self, _widget: Gtk.DrawingArea, event: Gdk.EventScroll) -> bool:
        """Forward scroll events to the applet under the cursor, if any.

        Hit-tests the cursor against the current layout to find the target
        item. If it's an applet, delegates to its on_scroll() and refreshes
        the tooltip (applet name/state may change on scroll, e.g. clippy).
        """
        frame = DockWindow._build_and_store_geometry_frame(
            self,
            cursor_x=event.x,
            cursor_y=event.y,
        )
        item = frame.item_at_point(event.x, event.y)
        if item and is_applet(desktop_id=item.desktop_id):
            applet = self.model.get_applet(item.desktop_id)
            if applet:
                direction_up = _scroll_direction_up(event=event)
                if direction_up is None:
                    return False
                applet.on_scroll(direction_up)
                # Refresh tooltip immediately (item.name may have changed)
                self.tooltip.update(item, frame)
                return True
        return False

    def _on_leave(self, widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        """Handle mouse leaving the dock area.

        This is the most complex event handler in the dock because it
        coordinates several subsystems: zoom state, preview popups,
        autohide, and cursor tracking.
        """
        log.debug(
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
        current_entry = self._cache.geometry_frame
        frame = (
            current_entry.frame if current_entry is not None else None
        ) or self._cache.applied_input_frame
        input_rect = current_input_rect(frame)
        if input_rect is not None and self.interaction.point_inside_event_frame(
            x=event.x, y=event.y
        ):
            return False

        if not self.dock_hovered:
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
        self.interaction.on_effective_leave(widget)
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
        frame = DockWindow._build_and_store_geometry_frame(
            self,
            cursor_x=event.x,
            cursor_y=event.y,
        )
        if frame.cursor_rect.contains(event.x, event.y):
            self.interaction.on_effective_enter()
        return True

    def _on_model_changed(self) -> None:
        """Reposition and redraw when the model changes."""
        DockWindow._invalidate_current_geometry_frame(self)
        self.update_input_region()
        self.hover.on_model_changed()
        # Refresh hover/tooltip state even without mouse motion so applets
        # that update item.name asynchronously (e.g. workspace switcher)
        # show the new tooltip text immediately.
        if self.hover.hovered_item is not None:
            cursor_main = (
                self.cursor_x if is_horizontal(pos=self.config.pos) else self.cursor_y
            )
            self.hover.update(cursor_main)
        DockWindow._bump_perf(self, "redraw_requests")
        DockWindow._bump_perf(self, "redraw_request_model_change")
        DockWindow._schedule_redraw(self)

    def update_input_region(self, frame: DockGeometryFrame | None = None) -> None:
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
        DockWindow._bump_perf(self, "update_input_region_calls")
        gdk_window = self.get_window()
        if not gdk_window:
            return
        frame = frame or DockWindow._current_or_build_geometry_frame(self)
        current_entry = self._cache.geometry_frame
        current_frame = current_entry.frame if current_entry is not None else None
        if frame is not current_frame:
            self._cache.store_geometry_frame(
                frame=frame,
                signature=DockWindow._geometry_signature(self),
            )
        old_rect = current_input_rect(self._cache.applied_input_frame)
        new_rect = frame.cursor_rect
        if new_rect != old_rect:
            region = cairo.Region(
                cairo.RectangleInt(new_rect.x, new_rect.y, new_rect.w, new_rect.h)
            )
            gdk_window.input_shape_combine_region(region, 0, 0)
            DockWindow._bump_perf(self, "input_region_native_writes")
            self._cache.applied_input_frame = frame

    def _sync_background_blur_hint(self, *, frame: DockGeometryFrame) -> None:
        DockWindow._bump_perf(self, "blur_sync_calls")
        gdk_window = self.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return

        if self.autohide.enabled and self.autohide.state == HideState.HIDDEN:
            if self._cache.last_blur_region is not None:
                clear_blur_region(gdk_window=gdk_window)
                DockWindow._bump_perf(self, "blur_region_clears")
                self._cache.last_blur_region = None
            return

        background = frame.background_rect
        blur_region = tuple(
            compute_blur_region(
                rect=BlurRect(
                    x=background.x,
                    y=background.y,
                    width=background.w,
                    height=background.h,
                ),
                roundness=self.theme.roundness,
                round_bottom=self.theme.round_bottom,
                position=self.config.pos,
                scale=gdk_window.get_scale_factor(),
            )
        )
        if blur_region == self._cache.last_blur_region:
            return
        set_blur_region(gdk_window=gdk_window, blur_region=list(blur_region))
        DockWindow._bump_perf(self, "blur_region_native_writes")
        self._cache.last_blur_region = blur_region

    def queue_redraw(self) -> None:
        """Convenience for external controllers to trigger redraw."""
        DockWindow._bump_perf(self, "redraw_requests")
        DockWindow._bump_perf(self, "redraw_request_queue_redraw")
        DockWindow._invalidate_current_geometry_frame(self)
        DockWindow._schedule_redraw(self)


def _scroll_direction_up(*, event: Gdk.EventScroll) -> bool | None:
    """Normalize GTK discrete and smooth scroll events to one applet direction."""
    if event.direction == Gdk.ScrollDirection.UP:
        return True
    if event.direction == Gdk.ScrollDirection.DOWN:
        return False
    if event.direction == Gdk.ScrollDirection.SMOOTH:
        has_deltas, _dx, dy = event.get_scroll_deltas()
        if not has_deltas or dy == 0:
            return None
        return dy < 0
    return None
