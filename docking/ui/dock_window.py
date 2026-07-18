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

"""GTK dock window shell that binds together rendering, geometry, and policy.

What this class is

`DockWindow` is the real GTK/X11 window that the user sees and interacts with.
It is not "the dock logic" in the abstract. It is the shell that:

- owns the GTK window and drawing area,
- exposes shared geometry/input-region helpers,
- hosts shell-level collaborators such as placement, hover, tooltip, and preview.

The UI graph itself is composed in `docking.ui.factory`.

What this class is not

`DockWindow` is no longer meant to directly implement every dock concern.
That decomposition matters because this file used to become the place where
every cross-feature fix landed. The current split is:

- DockWindow
  GTK shell and shared window geometry

- DockPlacementController
  monitor choice, reposition, struts, barriers, active-display polling

- DockInteractionCoordinator
  effective enter/leave, menu-open policy, preview-aware leave policy

- DockGeometryBuilder
  window state -> shared geometry frame

- HoverManager / TooltipManager / PreviewPopup / DockInputController
  focused feature owners

The point of this file is to provide the shell those pieces use, not to own
their higher-level behavior.

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

from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.applets.popup import PopupAnchor
from docking.core.position import Position
from docking.i18n import _
from docking.platform.backends.base import (
    PreviewService,
    Rect,
    SessionBackend,
    SurfaceService,
    WindowService,
)
from docking.platform.environment import detect_desktop, log_runtime_snapshot
from docking.ui import geometry
from docking.ui.autohide import AutoHideController, HideState
from docking.ui.display import window_screen_position
from docking.ui.effects import ZoomAnimator
from docking.ui.geometry import (
    DockGeometryBuilder,
    DockGeometryFrame,
    current_input_rect,
)
from docking.ui.hover import HoverManager
from docking.ui.interaction import DockInteractionCoordinator
from docking.ui.placement import DockPlacementController
from docking.ui.preview import PreviewPopup
from docking.ui.tooltip import TooltipManager

# Re-exported for existing callers/tests.
TRIGGER_PX = geometry.TRIGGER_PX
TRIGGER_PX_TOP = geometry.TRIGGER_PX_TOP
compute_input_rect = geometry.compute_input_rect

if TYPE_CHECKING:
    from docking.core.config import Config
    from docking.core.items import DockItem
    from docking.core.theme import Theme
    from docking.platform.backends.base import VisibilityMonitor
    from docking.platform.model import DockModel
    from docking.ui.renderer import DockRenderer


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
    last_blur_region: tuple[object, ...] | None = None

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


class DockWindow(Gtk.Window):
    """Top-level dock shell that owns window geometry and edge integration.

    Runtime UI controllers are composed outside this class. DockWindow keeps the
    GTK surface, placement, geometry, hover, tooltip, preview, and rendering
    state they share.
    """

    def __init__(
        self,
        config: Config,
        model: DockModel,
        renderer: DockRenderer,
        theme: Theme,
        window_tracker: WindowService,
        preview_service: PreviewService,
        surface_service: SurfaceService,
        session_backend: SessionBackend,
    ) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.config = config
        self.model = model
        self.renderer = renderer
        self.theme = theme
        self.window_tracker = window_tracker
        self.preview_service = preview_service
        self.surface_service = surface_service
        self.session_backend = session_backend
        self.cursor_x: float = -1.0
        self.cursor_y: float = -1.0
        self.autohide: AutoHideController
        self.preview: PreviewPopup
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

        self.placement = DockPlacementController(self, surface_service=surface_service)
        self.interaction = DockInteractionCoordinator(self)
        self._cache = _DockWindowCache.create()
        self._redraw_source_id: int | None = None
        self.dock_hovered: bool = False
        self._last_autohide_state: HideState | None = None
        self.dodge_monitor: VisibilityMonitor | None = None

        self._setup_window()
        self._setup_drawing_area()
        self.zoom_animator = ZoomAnimator(self.drawing_area)
        self._build_components()

    def _setup_window(self) -> None:
        """Configure GTK window as an X11 dock.

        Sets window manager hints (DOCK type, skip taskbar/pager, keep above,
        sticky) and enables RGBA visual for composited transparency.
        """
        self.set_title(_("Docking"))
        self.set_wmclass("Docking", "Docking")
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_resizable(False)
        self.surface_service.configure_before_realize(self)
        self.surface_service.set_workspace_scope(
            current_workspace_only=self.config.current_workspace_only
        )

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
            | Gdk.EventMask.SMOOTH_SCROLL_MASK
        )
        self.add(self.drawing_area)

    def _on_destroy(self, _window: Gtk.Window) -> None:
        """Release model subscriptions owned by the dock shell."""
        if self.dodge_monitor is not None:
            self.dodge_monitor.stop()
            self.dodge_monitor = None

    def set_theme(self, theme: Theme) -> None:
        """Replace the runtime theme and notify collaborators that cache it."""
        self.theme = theme
        self.tooltip.set_theme(theme)
        self.hover.set_theme(theme)
        self._invalidate_current_geometry_frame()

    def _build_components(self) -> None:
        """Build long-lived UI collaborators that depend on the live window."""
        self.autohide = AutoHideController(self, self.config)
        self.preview = PreviewPopup(
            window_tracker=self.window_tracker,
            preview_service=self.preview_service,
        )
        self.preview.set_transient_for(self)
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
            self._flush_scheduled_redraw,
        )

    def _geometry_signature(
        self,
        *,
        drop_insert_index: int = -1,
        cursor_x: float | None = None,
        cursor_y: float | None = None,
    ) -> tuple[object, ...]:
        window_w, window_h = self.get_size()
        resolved_x = self.cursor_x if cursor_x is None else cursor_x
        resolved_y = self.cursor_y if cursor_y is None else cursor_y
        autohide_enabled = self.autohide.enabled
        autohide_state = self.autohide.state if autohide_enabled else None
        autohide_zoom = self.autohide.zoom_progress if autohide_enabled else 1.0
        zoom_progress = self.zoom_animator.progress * autohide_zoom
        hide_offset = self.autohide.hide_offset if autohide_enabled else 0.0
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
            signature=self._geometry_signature(
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
        expected_signature = self._geometry_signature(
            drop_insert_index=drop_insert_index,
        )
        current_frame = self._cache.matching_geometry_frame(
            signature=expected_signature
        )
        if current_frame is not None:
            return current_frame
        return self._build_and_store_geometry_frame(
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
        window_pos = window_screen_position(self)
        win_x, win_y = window_pos.x, window_pos.y
        x, y = geometry.anchor_point(
            win_x=win_x,
            win_y=win_y,
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

    def popup_anchor_for_item(
        self,
        item: DockItem,
        frame: DockGeometryFrame,
    ) -> PopupAnchor | None:
        """Build the current outer-center popup anchor for one dock item."""
        item_geometry = frame.geometry_for_item(item)
        if item_geometry is None:
            return None
        window_pos = window_screen_position(self)
        anchor_x, anchor_y = item_geometry.anchor_point(
            win_x=window_pos.x,
            win_y=window_pos.y,
            position=self.config.pos,
        )
        if self.config.pos in (Position.BOTTOM, Position.TOP):
            anchor_x += int(item_geometry.draw_rect.w / 2)
        else:
            anchor_y += int(item_geometry.draw_rect.h / 2)
        return PopupAnchor(
            x=anchor_x,
            y=anchor_y,
            position=self.config.pos,
            parent=self,
        )

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
        frame = frame or self._current_or_build_geometry_frame()
        current_entry = self._cache.geometry_frame
        current_frame = current_entry.frame if current_entry is not None else None
        if frame is not current_frame:
            self._cache.store_geometry_frame(
                frame=frame,
                signature=self._geometry_signature(),
            )
        old_rect = current_input_rect(self._cache.applied_input_frame)
        new_rect = frame.cursor_rect
        if new_rect != old_rect:
            self.surface_service.update_input_region(
                Rect(
                    x=new_rect.x,
                    y=new_rect.y,
                    width=new_rect.w,
                    height=new_rect.h,
                )
            )
            self._cache.applied_input_frame = frame

    def _sync_background_blur_hint(self, *, frame: DockGeometryFrame) -> None:
        if self.autohide.enabled and self.autohide.state == HideState.HIDDEN:
            if self._cache.last_blur_region is not None:
                self.surface_service.set_blur_region(None)
                self._cache.last_blur_region = None
            return

        background = frame.background_rect
        blur_key = (
            background.x,
            background.y,
            background.w,
            background.h,
            self.theme.roundness,
            self.theme.round_bottom,
            self.config.pos,
            self.get_scale_factor(),
        )
        if blur_key == self._cache.last_blur_region:
            return
        self.surface_service.set_blur_region(
            Rect(
                x=background.x,
                y=background.y,
                width=background.w,
                height=background.h,
            )
        )
        self._cache.last_blur_region = blur_key

    def queue_redraw(self) -> None:
        """Convenience for external controllers to trigger redraw."""
        self._invalidate_current_geometry_frame()
        self._schedule_redraw()
