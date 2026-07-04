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

"""Dock placement, monitor choice, struts, barriers, and edge integration.

The dock must answer platform-facing questions that sit outside internal
geometry: which monitor to attach to, where to position the top-level window,
what screen space to reserve for maximized windows, when to update pointer
barriers, and how active-display mode follows the pointer between monitors.
These are not interaction policy and they are not rendering. This module owns
that platform and placement layer.

DockPlacementController owns monitor selection and monitor menu choices,
realization-time positioning, monitor and screen signal tracking, deferred
reposition scheduling, X11 strut application and clearing, pointer barrier
updates, and active-display polling.

It does not own hover policy, tooltip or preview behavior, drag and drop
behavior, item geometry, or Cairo rendering.

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

Placement does not always use the same rectangle for both axes. Along the dock
edge the monitor edge matters; along the perpendicular axis workarea may matter
more so the dock avoids conflicting with reserved desktop areas.

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

from dataclasses import dataclass
from typing import TYPE_CHECKING

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from docking.core.config import effective_edge_gap
from docking.core.position import Position, is_horizontal
from docking.i18n import _
from docking.log import get_logger
from docking.platform.backends.base import (
    MonitorSnapshot,
    PlacementRequest,
    Rect,
    ReservationRequest,
    Size,
    SurfaceService,
)
from docking.ui.display import get_pointer_position

if TYPE_CHECKING:
    from docking.ui.dock_window import DockWindow

log = get_logger(name="placement")


@dataclass(frozen=True)
class MonitorChoice:
    """One selectable monitor target for preferences and menus."""

    label: str
    index: int
    connector: str | None = None


class DockPlacementController:
    """Owns monitor selection, placement, struts, and pointer barriers."""

    def __init__(
        self,
        window: DockWindow,
        *,
        surface_service: SurfaceService,
    ) -> None:
        self._window = window
        self._surface = surface_service
        self._active_display_timer: int = 0
        self._active_monitor: Gdk.Monitor | None = None
        self._screen_signal_handlers: list[tuple[object, int]] = []
        self._geometry_refresh_source: int = 0

    def current_monitor_choice(self) -> int:
        """Current configured home monitor (-1=primary, >=0 specific monitor)."""
        display = self._window.get_display()
        if not display:
            return -1
        n_monitors = display.get_n_monitors()
        if n_monitors <= 0:
            return -1
        configured = self._monitor_for_connector(
            display=display,
            connector=getattr(self._window.config, "monitor_connector", None),
        )
        if configured is not None:
            return self._monitor_index(display=display, monitor=configured)
        selected = int(self._window.config.monitor_index)
        if selected == -1:
            return self.primary_monitor_index()
        if selected < 0 or selected >= n_monitors:
            return self.primary_monitor_index()
        return selected

    def get_monitor_menu_choices(self) -> list[tuple[str, int]]:
        """Monitor choices for menu display. Empty when only one monitor."""
        display = self._window.get_display()
        if not display or display.get_n_monitors() <= 1:
            return []
        choices = self.get_monitor_choices()
        return [(choice.label, choice.index) for choice in choices]

    def get_monitor_choices(self) -> list[MonitorChoice]:
        """Monitor choices with connector identity when GDK exposes it."""
        display = self._window.get_display()
        if not display:
            return []
        n_monitors = display.get_n_monitors()
        if n_monitors <= 0:
            return []

        primary = display.get_primary_monitor() or display.get_monitor(0)
        primary_idx = 0
        for idx in range(n_monitors):
            if display.get_monitor(idx) is primary:
                primary_idx = idx
                break

        choices: list[MonitorChoice] = []
        for idx in range(n_monitors):
            monitor = display.get_monitor(idx)
            if monitor is None:
                continue
            geom = monitor.get_geometry()
            connector = self._monitor_connector(monitor)
            model = self._monitor_model(monitor)
            label = _("Display {display}: {width}x{height}").format(
                display=idx + 1,
                width=geom.width,
                height=geom.height,
            )
            if connector:
                label += f" - {connector}"
            elif model:
                label += f" - {model}"
            if idx == primary_idx:
                label += f" ({_('Primary')})"
            choices.append(MonitorChoice(label=label, index=idx, connector=connector))
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
        self._surface.on_realize(self._window)
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
        self.stop_active_display()
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
        gap = effective_edge_gap(theme, config)
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
        self._surface.position_or_anchor(
            PlacementRequest(
                monitor=self._monitor_snapshot(
                    display=display,
                    monitor=monitor,
                    monitor_idx=monitor_idx,
                ),
                position=pos,
                x=win_x,
                y=win_y,
                size=Size(width=win_w, height=win_h),
                gap=gap,
                keep_above=True,
            )
        )

        self.update_barrier()

    def set_struts(self) -> None:
        """Reserve screen space for the dock via _NET_WM_STRUT_PARTIAL."""
        if self._window.config.hide_mode != "none":
            self.clear_struts()
            return

        display = self._window.get_display()
        monitor = self._resolve_target_monitor(display=display)
        if monitor is None:
            return
        icon_size = self._window.config.icon_size
        gap = effective_edge_gap(self._window.theme, self._window.config)
        strut_height = int(icon_size + self._window.theme.bottom_padding + gap)

        self._surface.set_reservation(
            ReservationRequest(
                monitor=self._monitor_snapshot(
                    display=display,
                    monitor=monitor,
                    monitor_idx=self._monitor_index(display=display, monitor=monitor),
                ),
                position=self._window.config.pos,
                thickness=strut_height,
            )
        )

    def update_barrier(self) -> None:
        """Create or destroy the pointer barrier based on autohide state."""
        position = self._window.config.pos
        if self._window.config.hide_mode in ("none", "always-on-top"):
            self._surface.update_pointer_barrier(
                monitor=None,
                position=position,
                enabled=False,
            )
            return
        display = self._window.get_display()
        monitor = self._resolve_target_monitor(display=display)
        if monitor is None:
            self._surface.update_pointer_barrier(
                monitor=None,
                position=position,
                enabled=False,
            )
            return
        config = self._window.config
        self._surface.update_pointer_barrier(
            monitor=self._monitor_snapshot(
                display=display,
                monitor=monitor,
                monitor_idx=self._monitor_index(display=display, monitor=monitor),
            ),
            position=position,
            enabled=True,
            pressure_callback=self._on_barrier_pressure
            if config.pressure_reveal_enabled
            else None,
            pressure_threshold=config.pressure_threshold,
        )

    def _on_barrier_pressure(self) -> None:
        """Reveal the dock when accumulated barrier pressure exceeds threshold."""
        autohide = getattr(self._window, "autohide", None)
        if autohide is None:
            return
        autohide.on_mouse_enter()

    def clear_struts(self) -> None:
        """Remove strut reservation by setting all struts to zero."""
        self._surface.clear_reservation()

    def update_struts(self) -> None:
        """Refresh struts and barrier after autohide toggle."""
        self.set_struts()
        self.update_barrier()

    def refresh_pressure_handler(self) -> None:
        """Refresh pointer-barrier pressure settings from current config."""
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

        configured = self._monitor_for_connector(
            display=display,
            connector=getattr(self._window.config, "monitor_connector", None),
        )
        if configured is not None:
            return configured

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

    @classmethod
    def _monitor_for_connector(
        cls, *, display: Gdk.Display, connector: str | None
    ) -> Gdk.Monitor | None:
        if not connector:
            return None
        try:
            n_monitors = display.get_n_monitors()
        except Exception:
            return None
        for idx in range(n_monitors):
            monitor = display.get_monitor(idx)
            if monitor is not None and cls._monitor_connector(monitor) == connector:
                return monitor
        return None

    @staticmethod
    def _monitor_connector(monitor: Gdk.Monitor) -> str | None:
        getter = getattr(monitor, "get_connector", None)
        if not callable(getter):
            return None
        text = str(getter() or "").strip()
        return text or None

    @staticmethod
    def _monitor_model(monitor: Gdk.Monitor) -> str | None:
        text = str(monitor.get_model() or "").strip()
        return text or None

    def _monitor_snapshot(
        self,
        *,
        display: Gdk.Display | None,
        monitor: Gdk.Monitor,
        monitor_idx: int,
    ) -> MonitorSnapshot:
        geom = monitor.get_geometry()
        workarea = monitor.get_workarea()
        primary = (
            display is not None
            and (display.get_primary_monitor() or display.get_monitor(0)) is monitor
        )
        return MonitorSnapshot(
            index=monitor_idx,
            geometry=Rect(
                x=geom.x,
                y=geom.y,
                width=geom.width,
                height=geom.height,
            ),
            workarea=Rect(
                x=workarea.x,
                y=workarea.y,
                width=workarea.width,
                height=workarea.height,
            ),
            scale=self._window.get_scale_factor(),
            primary=primary,
            name=self._monitor_model(monitor),
            connector=self._monitor_connector(monitor),
        )
