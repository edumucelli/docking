"""Dock placement, monitor choice, struts, barriers, and edge integration.

Why placement is its own module

The dock's visible behavior depends on more than its internal geometry. It also
has to answer platform-facing questions:

- which monitor should the dock attach to,
- where should the top-level window actually be moved,
- what screen space should be reserved for maximized windows,
- when should pointer barriers be updated,
- how should active-display mode follow the pointer between monitors.

Those concerns are related, but they are not interaction policy and they are
not rendering. This module owns that platform/placement layer.

What this module owns

DockPlacementController owns:

- monitor selection and monitor menu choices,
- realization-time positioning,
- monitor/screen signal tracking,
- deferred reposition scheduling,
- X11 strut application and clearing,
- pointer barrier updates,
- active-display polling.

It does not own:

- hover policy,
- tooltip/preview behavior,
- drag/drop behavior,
- item geometry,
- Cairo rendering.

Logical screen vs monitor geometry

Multi-monitor placement is the main reason this module exists. The dock must
distinguish:

- logical screen geometry
- individual monitor geometry
- current monitor workarea

ASCII example:

    logical screen
    +-----------------------------------------------+
    | monitor 0                 | monitor 1         |
    |                           |                   |
    |                           |                   |
    +-----------------------------------------------+

The dock may live on only one monitor, but X11 struts are expressed relative to
the logical screen edge. That mismatch is why placement and strut logic must be
coordinated carefully.

Workarea vs monitor bounds

Placement does not always use the same rectangle for both axes.

Why:
- along the dock edge, the monitor edge matters,
- along the perpendicular axis, workarea may matter more so the dock avoids
  conflicting with reserved desktop areas.

For example:

    bottom dock on a monitor
      |
      +--> X spans monitor width
      +--> Y aligns to bottom edge

    left dock
      |
      +--> X aligns to left edge
      +--> Y spans workarea height

That split is subtle but important for correct monitor-edge behavior.

Placement sequence

The normal flow is:

    window realized
      |
      +--> attach screen signals
      +--> initialize pointer barrier backend if available
      +--> compute target monitor
      +--> move/resize dock window
      +--> set struts if appropriate
      +--> refresh input region
      +--> start active-display polling if enabled

Reposition scheduling

Monitor and scale changes often arrive in bursts. Repositioning immediately on
every signal can cause redundant work and more geometry churn than necessary.

So this module uses deferred reposition:

    monitors-changed / size-changed / scale-factor-changed
      |
      +--> schedule_reposition()
      |
      +--> one idle callback performs the actual reposition

That keeps placement responsive without making every platform signal a full dock
re-layout in place.

Struts and barriers

Two platform-facing edge integrations live here:

1. Struts
   Reserve workspace so maximized windows do not cover an always-visible dock.

2. Pointer barriers
   Improve edge interaction by helping the pointer stop at the intended edge in
   some environments.

They are grouped here because they both depend on the final resolved monitor
placement of the dock window.

Active-display mode

Active-display mode means:

    "follow the monitor the pointer is currently on"

This is intentionally a placement concern, not an interaction concern. The dock
rebinds itself to a different monitor when the pointer moves across displays.

The loop is:

    poll pointer monitor
      |
      +--> if active monitor changed:
              reposition dock
              refresh edge integrations

That is why active-display logic belongs here instead of in the event/hover
stack.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk

from docking.core.position import Position, is_horizontal
from docking.i18n import _
from docking.log import get_logger
from docking.platform.barriers import PointerBarrier
from docking.platform.struts import clear_struts, set_dock_struts
from docking.ui.display import get_pointer_position

if TYPE_CHECKING:
    from docking.ui.dock_window import DockWindow

log = get_logger(name="placement")


class DockPlacementController:
    """Owns monitor selection, placement, struts, and pointer barriers."""

    def __init__(
        self,
        window: DockWindow,
        *,
        barrier: PointerBarrier | None = None,
    ) -> None:
        self._window = window
        self._barrier = barrier or PointerBarrier()
        self._active_display_timer: int = 0
        self._active_monitor: Gdk.Monitor | None = None
        self._screen_signal_handlers: list[tuple[object, int]] = []
        self._geometry_refresh_source: int = 0

    def current_monitor_choice(self) -> int:
        """Current monitor menu selection (-1=primary, >=0 specific monitor)."""
        display = self._window.get_display()
        if not display:
            return -1
        n_monitors = display.get_n_monitors()
        if n_monitors <= 0:
            return -1
        selected = int(self._window.config.monitor_index)
        if selected == -1:
            return self.primary_monitor_index()
        if selected < 0 or selected >= n_monitors:
            return self.primary_monitor_index()
        return selected

    def get_monitor_menu_choices(self) -> list[tuple[str, int]]:
        """Monitor choices for menu display. Empty when only one monitor."""
        display = self._window.get_display()
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
        display = self._window.get_display()
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

    def on_realize(self, *_args: object) -> None:
        """Position dock and set struts after window is realized."""
        self.attach_screen_signals(self._window.get_screen())
        display = self._window.get_display()
        if display and isinstance(display, GdkX11.X11Display):
            self._barrier.initialize(gdk_display=display)
        self.position_dock()
        self.set_struts()
        self._window.update_input_region()
        if self._window.config.active_display:
            self.start_active_display()

    def attach_screen_signals(self, screen: Gdk.Screen | None) -> None:
        self.disconnect_screen_signals()
        if screen is None:
            return
        self._screen_signal_handlers = [
            (
                screen,
                screen.connect("monitors-changed", self.on_screen_metrics_changed),
            ),
            (screen, screen.connect("size-changed", self.on_screen_metrics_changed)),
        ]

    def disconnect_screen_signals(self) -> None:
        for obj, handler_id in self._screen_signal_handlers:
            obj.disconnect(handler_id)
        self._screen_signal_handlers = []

    def on_screen_changed(
        self, _widget: Gtk.Widget, _previous_screen: Gdk.Screen | None
    ) -> None:
        self.attach_screen_signals(self._window.get_screen())
        self.schedule_reposition()

    def on_screen_metrics_changed(self, *_args: object) -> None:
        self.schedule_reposition()

    def on_scale_factor_changed(self, *_args: object) -> None:
        self.schedule_reposition()

    def schedule_reposition(self) -> None:
        if not self._window.get_realized():
            return
        if self._geometry_refresh_source:
            return
        self._geometry_refresh_source = GLib.idle_add(self.apply_scheduled_reposition)

    def apply_scheduled_reposition(self) -> bool:
        self._geometry_refresh_source = 0
        self.reposition()
        return False

    def on_destroy(self, *_args: object) -> None:
        refresh_source = self._geometry_refresh_source
        if refresh_source:
            GLib.source_remove(refresh_source)
            self._geometry_refresh_source = 0
        self.disconnect_screen_signals()

    def position_dock(self) -> None:
        """Position the dock window at the configured screen edge."""
        display = self._window.get_display()
        monitor = self._resolve_target_monitor(display=display)
        if monitor is None:
            return
        monitor_idx = self._monitor_index(display=display, monitor=monitor)
        geom = monitor.get_geometry()
        workarea = monitor.get_workarea()

        config = self._window.config
        theme = self._window.theme
        icon_size = config.icon_size
        zoom = config.zoom_percent if config.zoom_enabled else 1.0
        bounce_headroom = int(icon_size * theme.urgent_bounce_height)
        cross = int(
            icon_size * zoom
            + theme.top_padding
            + theme.bottom_padding
            + bounce_headroom
        )
        pos = config.pos
        gap = max(0, int(theme.distance_from_edge))
        if is_horizontal(pos=pos):
            win_w, win_h = geom.width, cross + gap
            if pos == Position.BOTTOM:
                win_x = geom.x
                win_y = geom.y + geom.height - win_h
            else:
                win_x = geom.x
                win_y = workarea.y
        else:
            win_w, win_h = cross + gap, workarea.height
            if pos == Position.LEFT:
                win_x = geom.x
                win_y = workarea.y
            else:
                win_x = geom.x + geom.width - win_w
                win_y = workarea.y

        log.debug(
            "dock position: monitor=%s geom=(%d,%d %dx%d) workarea=(%d,%d %dx%d) "
            "win=(%d,%d) size=%dx%d cross=%d bounce_headroom=%d",
            monitor_idx,
            geom.x,
            geom.y,
            geom.width,
            geom.height,
            workarea.x,
            workarea.y,
            workarea.width,
            workarea.height,
            win_x,
            win_y,
            win_w,
            win_h,
            cross,
            bounce_headroom,
        )
        self._window.set_size_request(win_w, win_h)
        self._window.resize(win_w, win_h)
        self._window.move(win_x, win_y)

        self.update_barrier()

    def set_struts(self) -> None:
        """Reserve screen space for the dock via _NET_WM_STRUT_PARTIAL."""
        if self._window.config.hide_mode != "none":
            self.clear_struts()
            return

        gdk_window = self._window.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return

        display = self._window.get_display()
        monitor = self._resolve_target_monitor(display=display)
        if monitor is None:
            return
        geom = monitor.get_geometry()
        screen = self._window.get_screen()

        icon_size = self._window.config.icon_size
        gap = max(0, int(self._window.theme.distance_from_edge))
        strut_height = int(icon_size + self._window.theme.bottom_padding + gap)

        set_dock_struts(
            gdk_window=gdk_window,
            dock_height=strut_height,
            monitor_geom=geom,
            screen=screen,
            position=self._window.config.pos,
        )

    def update_barrier(self) -> None:
        """Create or destroy the pointer barrier based on autohide state."""
        if not self._barrier.supported:
            return
        if self._window.config.hide_mode == "none":
            self._barrier.destroy()
            return
        display = self._window.get_display()
        monitor = self._resolve_target_monitor(display=display)
        if monitor is None:
            self._barrier.destroy()
            return
        geom = monitor.get_geometry()
        self._barrier.update(
            position=self._window.config.pos,
            monitor_x=geom.x,
            monitor_y=geom.y,
            monitor_w=geom.width,
            monitor_h=geom.height,
        )

    def clear_struts(self) -> None:
        """Remove strut reservation by setting all struts to zero."""
        gdk_window = self._window.get_window()
        if not gdk_window or not isinstance(gdk_window, GdkX11.X11Window):
            return
        clear_struts(gdk_window=gdk_window)

    def update_struts(self) -> None:
        """Refresh struts and barrier after autohide toggle."""
        self.set_struts()
        self.update_barrier()

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

    def reposition(self) -> None:
        """Re-layout after position change -- reposition window, struts, input."""
        self.position_dock()
        self.set_struts()
        self._window.update_input_region()
        self._window.drawing_area.queue_draw()

    def _poll_active_display(self) -> bool:
        """Poll cursor position and move dock to the monitor under cursor."""
        display = self._window.get_display()
        if not display:
            return True
        n_monitors = display.get_n_monitors()
        pos = get_pointer_position(display)
        if pos is None:
            log.debug("active-display poll: no pointer position available")
            return True
        monitor_summaries: list[str] = []
        for idx in range(n_monitors):
            candidate = display.get_monitor(idx)
            if candidate is None:
                monitor_summaries.append(f"{idx}=<none>")
                continue
            geom = candidate.get_geometry()
            monitor_summaries.append(
                f"{idx}=({geom.x},{geom.y} {geom.width}x{geom.height})"
            )
        monitor = display.get_monitor_at_point(pos.x, pos.y)
        resolved_idx = self._monitor_index(display=display, monitor=monitor)
        active_idx = self._monitor_index(display=display, monitor=self._active_monitor)
        log.debug(
            "active-display poll: pointer=(%d,%d) monitors=%d [%s] "
            "resolved=%s previous=%s",
            pos.x,
            pos.y,
            n_monitors,
            ", ".join(monitor_summaries),
            resolved_idx,
            active_idx,
        )
        if monitor is not None and monitor != self._active_monitor:
            self._active_monitor = monitor
            log.debug("active-display poll: switching to monitor=%s", resolved_idx)
            self.reposition()
            return True
        return True

    def _resolve_target_monitor(self, display: Gdk.Display) -> Gdk.Monitor | None:
        """Resolve configured monitor, falling back to primary monitor."""
        if self._window.config.active_display and self._active_monitor is not None:
            return self._active_monitor

        n_monitors = display.get_n_monitors()
        if n_monitors <= 0:
            return None

        selected = int(self._window.config.monitor_index)
        if 0 <= selected < n_monitors:
            monitor = display.get_monitor(selected)
            if monitor is not None:
                return monitor

        return display.get_primary_monitor() or display.get_monitor(0)

    @staticmethod
    def _monitor_index(
        *, display: Gdk.Display | None, monitor: Gdk.Monitor | None
    ) -> int:
        if display is None or monitor is None:
            return -1
        n_monitors = display.get_n_monitors()
        for idx in range(n_monitors):
            if display.get_monitor(idx) is monitor:
                return idx
        return -1
