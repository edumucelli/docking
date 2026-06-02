#!/usr/bin/env python3
"""Minimal GTK3/X11 dock-style repro for XWayland redraw freezes.

This file is not meant to behave exactly like Docking. It is a reduction tool.
The point is to keep a small, controllable matrix of window traits and
interaction patterns so redraw failures can be isolated to specific features.

Use it when you need to answer questions like:

- does the freeze require a dock-type toplevel, or does a normal window fail too?
- does it depend on RGBA transparency?
- does it require keep-above / sticky hints?
- is autohide transition enough, or is motion churn also required?
- do timed redraw nudges or tick pumping change the outcome?
- is the visible failure a fully transparent frame, or just the last frame
  staying stuck on screen while draw callbacks stop?

Recommended workflow
--------------------

1. Run the repro under the same backend as Docking.

       GDK_BACKEND=x11 python3 tools/xwayland_repro.py

2. Start from the most Docking-like configuration:

       XWAYLAND_REPRO_DOCK_HINT=1
       XWAYLAND_REPRO_KEEP_ABOVE=1
       XWAYLAND_REPRO_STICK=1
       XWAYLAND_REPRO_RGBA=1
       XWAYLAND_REPRO_CENTERED=0
       XWAYLAND_REPRO_AUTOHIDE=animate
       XWAYLAND_REPRO_TICK_PUMP=1
       XWAYLAND_REPRO_TRACE=1

3. Change one feature at a time and compare logs:

- window traits:
  `XWAYLAND_REPRO_DOCK_HINT`, `XWAYLAND_REPRO_KEEP_ABOVE`,
  `XWAYLAND_REPRO_STICK`, `XWAYLAND_REPRO_RGBA`, `XWAYLAND_REPRO_CENTERED`
- interaction load:
  `XWAYLAND_REPRO_AUTOHIDE`, `XWAYLAND_REPRO_AUTOCYCLE`,
  `XWAYLAND_REPRO_MOTION_SPAM`
- redraw forcing:
  `XWAYLAND_REPRO_TICK_PUMP`, `XWAYLAND_REPRO_RECOVER`,
  `XWAYLAND_REPRO_WATCHDOG_MS`

4. Interpret the trace conservatively:

- `queue-draw` continues but `draw-begin` stops:
  the app logic is still alive, but GTK/XWayland stopped delivering paints.
- `redraw-stalled` appears repeatedly:
  queued redraws are not reaching the draw callback within the watchdog window.
- `tick-pump` / `tick-pump-keepalive` continue but `draw-begin` stops:
  even forced frame activity is not recovering paint delivery.
- `runtime-expired` with continued `draw-end` entries up to the end:
  that run stayed healthy for the tested matrix combination.

5. Reduce toward the smallest failing case.

The best upstream bug report is the smallest combination of:
- window flags
- autohide mode
- motion pattern
- backend/session

that still reproduces the issue.

Current investigation notes
---------------------------

The main Docking traces established two important points:

- tooltip popups can remain alive after the dock stops repainting, so "tooltip
  still appears" does not mean the dock window is still drawing
- some failures are not "transparent new frames"; they are frozen last frames,
  for example a dock stuck visually mid-hover while the app keeps processing
  hover and tooltip updates

This repro therefore aims to answer two separate questions:

- does `draw` stop entirely?
- or does `draw` continue while the visible result becomes wrong?

It still does not model every Docking feature. In particular, the full app also
has tooltip popups, input-shape updates, blur hints, and richer hover/zoom
work. If this repro stays healthy while Docking fails, those missing features
remain candidates for the trigger.

Useful toggles
--------------

    XWAYLAND_REPRO_TRACE=1
    XWAYLAND_REPRO_TICK_PUMP=1
    XWAYLAND_REPRO_WATCHDOG_MS=250
    XWAYLAND_REPRO_AUTOHIDE=animate   # off | snap | animate
    XWAYLAND_REPRO_AUTOCYCLE=1
    XWAYLAND_REPRO_CYCLE_MS=900
    XWAYLAND_REPRO_RUNTIME_MS=12000
    XWAYLAND_REPRO_RECOVER=1
    XWAYLAND_REPRO_DOCK_HINT=1
    XWAYLAND_REPRO_KEEP_ABOVE=1
    XWAYLAND_REPRO_STICK=1
    XWAYLAND_REPRO_RGBA=1
    XWAYLAND_REPRO_CENTERED=0
    XWAYLAND_REPRO_MOTION_SPAM=1
    XWAYLAND_REPRO_OFFSCREEN_BLIT=1
    XWAYLAND_REPRO_INPUT_SHAPE=1
    XWAYLAND_REPRO_BLUR_HINT=1
"""

from __future__ import annotations

import contextlib
import math
import os
import sys

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib, Gtk

from docking.core.position import Position
from docking.platform.backends.x11.impl.struts import (
    BlurRect,
    clear_blur_region,
    compute_blur_region,
    set_blur_region,
)
from docking.ui.display import get_pointer_position

TRUE_VALUES = {"1", "true", "yes", "on"}
TRACE = os.environ.get("XWAYLAND_REPRO_TRACE", "1").strip().lower() in TRUE_VALUES
WATCHDOG_MS = int(os.environ.get("XWAYLAND_REPRO_WATCHDOG_MS", "250"))
AUTOHIDE_MODE = os.environ.get("XWAYLAND_REPRO_AUTOHIDE", "animate").strip().lower()
TICK_PUMP = (
    os.environ.get("XWAYLAND_REPRO_TICK_PUMP", "1").strip().lower() in TRUE_VALUES
)
RECOVER = os.environ.get("XWAYLAND_REPRO_RECOVER", "0").strip().lower() in TRUE_VALUES
AUTOCYCLE = (
    os.environ.get("XWAYLAND_REPRO_AUTOCYCLE", "1").strip().lower() in TRUE_VALUES
)
CYCLE_MS = int(os.environ.get("XWAYLAND_REPRO_CYCLE_MS", "900"))
RUNTIME_MS = int(os.environ.get("XWAYLAND_REPRO_RUNTIME_MS", "12000"))
DOCK_HINT = (
    os.environ.get("XWAYLAND_REPRO_DOCK_HINT", "1").strip().lower() in TRUE_VALUES
)
KEEP_ABOVE = (
    os.environ.get("XWAYLAND_REPRO_KEEP_ABOVE", "1").strip().lower() in TRUE_VALUES
)
STICKY = os.environ.get("XWAYLAND_REPRO_STICK", "1").strip().lower() in TRUE_VALUES
USE_RGBA = os.environ.get("XWAYLAND_REPRO_RGBA", "1").strip().lower() in TRUE_VALUES
CENTERED = os.environ.get("XWAYLAND_REPRO_CENTERED", "0").strip().lower() in TRUE_VALUES
MOTION_SPAM = (
    os.environ.get("XWAYLAND_REPRO_MOTION_SPAM", "0").strip().lower() in TRUE_VALUES
)
OFFSCREEN_BLIT = (
    os.environ.get("XWAYLAND_REPRO_OFFSCREEN_BLIT", "1").strip().lower() in TRUE_VALUES
)
INPUT_SHAPE = (
    os.environ.get("XWAYLAND_REPRO_INPUT_SHAPE", "1").strip().lower() in TRUE_VALUES
)
BLUR_HINT = (
    os.environ.get("XWAYLAND_REPRO_BLUR_HINT", "1").strip().lower() in TRUE_VALUES
)
FRAME_INTERVAL_MS = 16
TRIGGER_HEIGHT = 3
HIDDEN_TRIGGER_HEIGHT = 10
WINDOW_BG_ALPHA = 0.0
DOCK_HEIGHT = 84
DOCK_MARGIN = 18
WINDOW_HEIGHT = DOCK_HEIGHT + DOCK_MARGIN
POINTER_REVEAL_MARGIN = 2
RECOVERY_THRESHOLD = 3
ROUNDNESS = 22.0
ROUND_BOTTOM = True
MOTION_IDLE_HIDE_MS = 120


def log(message: str) -> None:
    if TRACE:
        print(message, flush=True)


def is_wayland_session() -> bool:
    return os.environ.get("XDG_SESSION_TYPE", "").strip().lower() == "wayland"


def is_x11_backend(display: object | None) -> bool:
    if display is None:
        return False
    cls = display.__class__
    if cls.__name__ == "X11Display":
        return True
    if cls.__module__.endswith("GdkX11"):
        return True
    return callable(getattr(display, "get_xdisplay", None))


def is_xwayland_session(display: object | None) -> bool:
    return is_wayland_session() and is_x11_backend(display)


class ReproWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="Docking XWayland Repro")
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        if STICKY:
            self.stick()
        self.set_keep_above(KEEP_ABOVE)
        self.set_type_hint(
            Gdk.WindowTypeHint.DOCK if DOCK_HINT else Gdk.WindowTypeHint.NORMAL
        )
        self.set_app_paintable(True)
        self.set_resizable(False)

        screen = self.get_screen()
        visual = (
            screen.get_rgba_visual() or screen.get_system_visual()
            if USE_RGBA
            else screen.get_system_visual()
        )
        self.set_visual(visual)

        self.area = Gtk.DrawingArea()
        # Match Docking more closely: render ourselves and avoid GTK's extra
        # intermediate buffering path for the transparent dock window.
        self.area.set_double_buffered(False)
        self.area.set_size_request(-1, WINDOW_HEIGHT)
        self.area.set_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
        )
        self.add(self.area)

        self.request_seq = 0
        self.draw_seq = 0
        self.redraw_watchdog_id = 0
        self.tick_id = 0
        self.keepalive_id = 0
        self.last_reason = "startup"
        self.stall_count = 0
        self.hovered = False
        self.pointer_x = -1.0
        self.pointer_y = -1.0
        self.last_motion_us = GLib.get_monotonic_time()
        self.cycle_target_visible = False
        self.hide_offset = 0.0
        self.autohide_state = "visible"
        self.anim_progress = 0.0
        self.anim_id = 0
        self.autocycle_id = 0
        self.runtime_id = 0
        self.motion_id = 0
        self.motion_phase = 0.0
        self.applied_input_rect: tuple[int, int, int, int] | None = None
        self.applied_blur_region: tuple[int, ...] | None = None

        self.connect("realize", self.on_realize)
        self.connect("configure-event", self.on_configure)
        self.connect("map-event", self.on_map)
        self.connect("unmap-event", self.on_unmap)
        self.connect("destroy", self.on_destroy)
        self.area.connect("draw", self.on_draw)
        self.area.connect("motion-notify-event", self.on_motion)
        self.area.connect("enter-notify-event", self.on_enter)
        self.area.connect("leave-notify-event", self.on_leave)

    def log_event(self, event: str, extra: str = "") -> None:
        alloc = self.area.get_allocation()
        try:
            win_x, win_y = self.get_position()
        except Exception:
            win_x, win_y = -1, -1
        try:
            win_w, win_h = self.get_size()
        except Exception:
            win_w, win_h = -1, -1
        suffix = f" {extra}" if extra else ""
        log(
            "paint-trace: event="
            f"{event}{suffix} "
            f"realized={self.get_realized()} visible={self.get_visible()} "
            f"mapped={self.get_mapped()} "
            f"window=({win_x},{win_y} {win_w}x{win_h}) "
            f"area={alloc.width}x{alloc.height} "
            f"autohide={self.autohide_state} hide_offset={self.hide_offset:.3f}"
        )

    def on_realize(self, *_args: object) -> None:
        display = self.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geometry = monitor.get_geometry()
        scale = monitor.get_scale_factor()
        width = geometry.width
        height = WINDOW_HEIGHT
        self.set_default_size(width, height)
        if CENTERED:
            self.set_position(Gtk.WindowPosition.CENTER)
        else:
            self.move(geometry.x, geometry.y + geometry.height - height)
        self.log_event(
            "realize",
            extra=(
                f"scale={scale} session_type={os.environ.get('XDG_SESSION_TYPE', '-')}"
                f" xwayland={is_xwayland_session(display)}"
                f" dock_hint={DOCK_HINT} keep_above={KEEP_ABOVE}"
                f" sticky={STICKY} rgba={USE_RGBA} centered={CENTERED}"
            ),
        )
        if TICK_PUMP:
            self.start_tick_pump()
        if AUTOCYCLE and AUTOHIDE_MODE != "off" and CYCLE_MS > 0:
            self.autocycle_id = GLib.timeout_add(CYCLE_MS, self.on_autocycle_tick)
        if RUNTIME_MS > 0:
            self.runtime_id = GLib.timeout_add(RUNTIME_MS, self.on_runtime_expired)
        if MOTION_SPAM:
            self.motion_id = GLib.timeout_add(FRAME_INTERVAL_MS, self.on_motion_tick)

    def on_configure(self, _widget: Gtk.Window, event: Gdk.EventConfigure) -> bool:
        self.log_event(
            "configure",
            extra=f"x={event.x} y={event.y} w={event.width} h={event.height}",
        )
        return False

    def on_map(self, _widget: Gtk.Window, _event: Gdk.EventAny) -> bool:
        self.log_event("map")
        return False

    def on_unmap(self, _widget: Gtk.Window, _event: Gdk.EventAny) -> bool:
        self.log_event("unmap")
        return False

    def on_runtime_expired(self) -> bool:
        self.log_event("runtime-expired")
        self.destroy()
        Gtk.main_quit()
        return False

    def on_autocycle_tick(self) -> bool:
        self.cycle_target_visible = not self.cycle_target_visible
        self.log_event(
            "autocycle",
            extra=f"target={'visible' if self.cycle_target_visible else 'hidden'}",
        )
        if self.cycle_target_visible:
            self.trigger_show()
        else:
            self.trigger_hide()
        return True

    def on_motion_tick(self) -> bool:
        alloc = self.area.get_allocation()
        width = max(alloc.width, 1)
        height = max(alloc.height, 1)
        self.motion_phase = (self.motion_phase + 0.045) % 1.0
        self.pointer_x = 24.0 + (self.motion_phase * max(width - 48.0, 1.0))
        self.pointer_y = height - (TRIGGER_HEIGHT + POINTER_REVEAL_MARGIN + 8.0)
        self.hovered = self.hide_offset < 1.0
        self.queue_redraw(reason="motion-spam")
        return True

    def on_destroy(self, *_args: object) -> None:
        if self.redraw_watchdog_id:
            GLib.source_remove(self.redraw_watchdog_id)
            self.redraw_watchdog_id = 0
        if self.anim_id:
            GLib.source_remove(self.anim_id)
            self.anim_id = 0
        if self.autocycle_id:
            GLib.source_remove(self.autocycle_id)
            self.autocycle_id = 0
        if self.runtime_id:
            GLib.source_remove(self.runtime_id)
            self.runtime_id = 0
        if self.motion_id:
            GLib.source_remove(self.motion_id)
            self.motion_id = 0
        if self.keepalive_id:
            GLib.source_remove(self.keepalive_id)
            self.keepalive_id = 0
        if self.tick_id:
            with contextlib.suppress(Exception):
                self.area.remove_tick_callback(self.tick_id)
            self.tick_id = 0
        gdk_window = self.get_window()
        if (
            BLUR_HINT
            and gdk_window is not None
            and isinstance(gdk_window, GdkX11.X11Window)
            and self.applied_blur_region is not None
        ):
            clear_blur_region(gdk_window=gdk_window)
            self.applied_blur_region = None

    def start_tick_pump(self) -> None:
        if self.tick_id == 0:
            self.tick_id = self.area.add_tick_callback(self.on_tick)
            self.log_event("tick-pump-start")
        if self.keepalive_id == 0:
            self.keepalive_id = GLib.timeout_add(
                FRAME_INTERVAL_MS,
                self.on_tick_keepalive,
            )

    def on_tick(self, widget: Gtk.Widget, _frame_clock: Gdk.FrameClock) -> bool:
        self.log_event(
            "tick-pump",
            extra=f"draw_seq={self.draw_seq} request_seq={self.request_seq}",
        )
        widget.queue_draw()
        return True

    def on_tick_keepalive(self) -> bool:
        self.reconcile_pointer_hover()
        self.log_event(
            "tick-pump-keepalive",
            extra=f"draw_seq={self.draw_seq} request_seq={self.request_seq}",
        )
        self.area.queue_draw()
        self.nudge_paint(reason="keepalive")
        return True

    def reconcile_pointer_hover(self) -> None:
        if AUTOHIDE_MODE == "off":
            return
        now_us = GLib.get_monotonic_time()
        if (
            self.autohide_state in {"visible", "showing"}
            and (now_us - self.last_motion_us) >= (MOTION_IDLE_HIDE_MS * 1000)
            and (self.hovered or self.autohide_state != "hiding")
        ):
            self.hovered = False
            self.log_event(
                "motion-idle-hide",
                extra=f"idle_ms={(now_us - self.last_motion_us) / 1000.0:.1f}",
            )
            self.trigger_hide()
        display = self.get_display()
        if display is None:
            return
        pos = get_pointer_position(display)
        if pos is None:
            return
        try:
            win_x, win_y = self.get_position()
        except Exception:
            return
        local_x = pos.x - win_x
        local_y = pos.y - win_y
        inside = self.point_inside_active_input(x=local_x, y=local_y)
        if inside:
            self.pointer_x = float(local_x)
            self.pointer_y = float(local_y)
            if not self.hovered:
                self.hovered = True
                self.log_event(
                    "pointer-reconcile-enter",
                    extra=f"x={local_x:.1f} y={local_y:.1f}",
                )
            if self.hide_offset > 0.0 or self.autohide_state == "hidden":
                self.trigger_show()
            return
        should_hide = self.hovered or self.autohide_state in {"visible", "showing"}
        if should_hide:
            self.hovered = False
            self.log_event(
                "pointer-reconcile-leave",
                extra=f"x={local_x:.1f} y={local_y:.1f}",
            )
            self.trigger_hide()

    def queue_redraw(self, reason: str) -> None:
        self.request_seq += 1
        self.last_reason = reason
        self.log_event(
            "queue-draw",
            extra=f"request_seq={self.request_seq} reason={reason}",
        )
        self.area.queue_draw()
        self.nudge_paint(reason=reason)
        self.schedule_watchdog(reason=reason)

    def nudge_paint(self, reason: str) -> None:
        ok = False
        frame_clock = self.area.get_frame_clock()
        if frame_clock is not None:
            frame_clock.request_phase(Gdk.FrameClockPhase.PAINT)
            ok = True
        window = self.area.get_window()
        if window is not None:
            alloc = self.area.get_allocation()
            rect = Gdk.Rectangle()
            rect.x = 0
            rect.y = 0
            rect.width = alloc.width
            rect.height = alloc.height
            window.invalidate_rect(rect, False)
            ok = True
        self.log_event(
            "paint-nudge",
            extra=f"request_seq={self.request_seq} reason={reason} ok={ok}",
        )

    def schedule_watchdog(self, reason: str) -> None:
        if WATCHDOG_MS <= 0:
            return
        if self.redraw_watchdog_id:
            GLib.source_remove(self.redraw_watchdog_id)
        request_seq = self.request_seq
        draw_seq = self.draw_seq
        self.redraw_watchdog_id = GLib.timeout_add(
            WATCHDOG_MS,
            self.on_redraw_watchdog,
            request_seq,
            draw_seq,
            reason,
        )

    def on_redraw_watchdog(
        self, request_seq: int, draw_seq_at_request: int, reason: str
    ) -> bool:
        self.redraw_watchdog_id = 0
        if self.draw_seq != draw_seq_at_request:
            self.stall_count = 0
            return False
        self.stall_count += 1
        self.log_event(
            "redraw-stalled",
            extra=(
                f"request_seq={request_seq} reason={reason} draw_seq={self.draw_seq}"
            ),
        )
        if RECOVER and self.stall_count >= RECOVERY_THRESHOLD:
            self.stall_count = 0
            self.log_event(
                "xwayland-recover",
                extra=(
                    f"request_seq={request_seq} reason=redraw-stalled:{reason} "
                    f"draw_seq={self.draw_seq}"
                ),
            )
            self.area.hide()
            self.area.show()
            self.show_all()
            self.queue_resize()
            self.area.queue_draw()
            self.nudge_paint(reason="recover")
        return False

    def on_draw(self, widget: Gtk.DrawingArea, cr: cairo.Context) -> bool:
        self.draw_seq += 1
        self.stall_count = 0
        if self.redraw_watchdog_id:
            GLib.source_remove(self.redraw_watchdog_id)
            self.redraw_watchdog_id = 0
        self.log_event(
            "draw-begin",
            extra=(
                f"draw_seq={self.draw_seq} request_seq={self.request_seq} "
                f"reason={self.last_reason}"
            ),
        )
        alloc = widget.get_allocation()
        width = alloc.width
        height = alloc.height

        if OFFSCREEN_BLIT:
            target = cr.get_target()
            offscreen = target.create_similar(cairo.Content.COLOR_ALPHA, width, height)
            ocr = cairo.Context(offscreen)
            self._draw_content(cr=ocr, width=width, height=height)
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_surface(offscreen, 0, 0)
            cr.paint()
        else:
            self._draw_content(cr=cr, width=width, height=height)

        self.update_input_shape(width=width, height=height)
        self.update_blur_hint(width=width, height=height)
        self.log_event("draw-end", extra=f"draw_seq={self.draw_seq}")
        return True

    def current_dock_y(self, *, height: int) -> float:
        visible_y = height - DOCK_HEIGHT - DOCK_MARGIN
        hidden_y = height - TRIGGER_HEIGHT
        return visible_y + ((hidden_y - visible_y) * self.hide_offset)

    def compute_input_rect(
        self, *, width: int, height: int
    ) -> tuple[int, int, int, int]:
        dock_w = min(width - 40, 920)
        dock_x = int((width - dock_w) / 2.0)
        if self.autohide_state == "hidden":
            return (
                0,
                max(0, height - HIDDEN_TRIGGER_HEIGHT),
                max(1, width),
                HIDDEN_TRIGGER_HEIGHT,
            )
        dock_y = int(self.current_dock_y(height=height))
        return (
            dock_x,
            max(0, dock_y),
            int(dock_w),
            min(height, DOCK_HEIGHT),
        )

    def point_inside_active_input(self, *, x: float, y: float) -> bool:
        alloc = self.area.get_allocation()
        width = max(alloc.width, 1)
        height = max(alloc.height, 1)
        dock_w = min(width - 40, 920)
        dock_x = int((width - dock_w) / 2.0)
        if self.autohide_state == "hidden":
            rect = (
                0,
                max(0, height - HIDDEN_TRIGGER_HEIGHT),
                width,
                HIDDEN_TRIGGER_HEIGHT,
            )
        else:
            dock_y = int(self.current_dock_y(height=height))
            rect = (
                dock_x,
                max(0, dock_y),
                int(dock_w),
                min(height, DOCK_HEIGHT),
            )
        rx, ry, rw, rh = rect
        return rx <= x < (rx + rw) and ry <= y < (ry + rh)

    def update_input_shape(self, *, width: int, height: int) -> None:
        if not INPUT_SHAPE:
            return
        window = self.get_window()
        if window is None:
            return
        rect = self.compute_input_rect(width=width, height=height)
        if rect == self.applied_input_rect:
            return
        x, y, w, h = rect
        region = cairo.Region(cairo.RectangleInt(x, y, w, h))
        window.input_shape_combine_region(region, 0, 0)
        self.applied_input_rect = rect
        self.log_event(
            "input-shape",
            extra=f"rect=({x},{y} {w}x{h})",
        )

    def update_blur_hint(self, *, width: int, height: int) -> None:
        if not BLUR_HINT:
            return
        gdk_window = self.get_window()
        if gdk_window is None or not isinstance(gdk_window, GdkX11.X11Window):
            return
        if self.hide_offset >= 1.0:
            if self.applied_blur_region is not None:
                clear_blur_region(gdk_window=gdk_window)
                self.applied_blur_region = None
                self.log_event("blur-clear")
            return
        dock_w = min(width - 40, 920)
        dock_x = int((width - dock_w) / 2.0)
        dock_y = int(self.current_dock_y(height=height))
        blur_region = tuple(
            compute_blur_region(
                rect=BlurRect(
                    x=dock_x,
                    y=dock_y,
                    width=int(dock_w),
                    height=DOCK_HEIGHT,
                ),
                roundness=ROUNDNESS,
                round_bottom=ROUND_BOTTOM,
                position=Position.BOTTOM,
                scale=gdk_window.get_scale_factor(),
            )
        )
        if blur_region == self.applied_blur_region:
            return
        set_blur_region(gdk_window=gdk_window, blur_region=list(blur_region))
        self.applied_blur_region = blur_region
        self.log_event(
            "blur-set",
            extra=(
                f"rect=({dock_x},{dock_y} {int(dock_w)}x{DOCK_HEIGHT}) "
                f"scale={gdk_window.get_scale_factor()}"
            ),
        )

    def _draw_content(self, *, cr: cairo.Context, width: int, height: int) -> None:
        """Paint the visible dock content onto the provided Cairo context."""

        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0.0, 0.0, 0.0, WINDOW_BG_ALPHA)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        dock_w = min(width - 40, 920)
        dock_h = DOCK_HEIGHT
        dock_x = (width - dock_w) / 2.0
        dock_y = self.current_dock_y(height=height)

        self.rounded_rect(cr, dock_x, dock_y, dock_w, dock_h, 22.0)
        cr.set_source_rgba(0.08, 0.1, 0.14, 0.92)
        cr.fill_preserve()
        cr.set_source_rgba(0.7, 0.82, 0.95, 0.2)
        cr.set_line_width(1.0)
        cr.stroke()

        slot_w = 72.0
        slot_gap = 10.0
        slot_y = dock_y + 14.0
        start_x = dock_x + 18.0
        hover_index = -1
        if self.hide_offset < 1.0 and self.pointer_y >= 0:
            for index in range(8):
                slot_x = start_x + index * (slot_w + slot_gap)
                if slot_x <= self.pointer_x <= slot_x + slot_w:
                    hover_index = index
                    break
        for index in range(8):
            slot_x = start_x + index * (slot_w + slot_gap)
            alpha = 0.2 if index != hover_index else 0.85
            lift = 0.0 if index != hover_index else -8.0
            self.rounded_rect(cr, slot_x, slot_y + lift, slot_w, 52.0, 18.0)
            if index == hover_index:
                cr.set_source_rgba(0.96, 0.68, 0.32, alpha)
            else:
                cr.set_source_rgba(0.85, 0.9, 0.95, alpha)
            cr.fill()

    def on_motion(self, _widget: Gtk.DrawingArea, event: Gdk.EventMotion) -> bool:
        self.last_motion_us = GLib.get_monotonic_time()
        self.pointer_x = event.x
        self.pointer_y = event.y
        self.log_event(
            "motion",
            extra=f"x={event.x:.1f} y={event.y:.1f}",
        )
        if self.point_inside_active_input(x=event.x, y=event.y):
            self.hovered = True
            if self.hide_offset > 0.0:
                self.trigger_show()
        self.queue_redraw(reason="motion")
        return False

    def on_enter(self, _widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        if not self.point_inside_active_input(x=event.x, y=event.y):
            self.log_event(
                "enter-ignored",
                extra=f"x={event.x:.1f} y={event.y:.1f}",
            )
            return False
        self.hovered = True
        self.last_motion_us = GLib.get_monotonic_time()
        self.pointer_x = event.x
        self.pointer_y = event.y
        self.log_event("enter", extra=f"x={event.x:.1f} y={event.y:.1f}")
        self.trigger_show()
        return False

    def on_leave(self, _widget: Gtk.DrawingArea, event: Gdk.EventCrossing) -> bool:
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        if self.point_inside_active_input(x=event.x, y=event.y):
            self.log_event(
                "leave-ignored",
                extra=(
                    f"detail={event.detail.value_nick} x={event.x:.1f} y={event.y:.1f}"
                ),
            )
            return False
        self.hovered = False
        self.log_event("leave", extra=f"detail={event.detail.value_nick}")
        self.trigger_hide()
        return False

    def trigger_show(self) -> None:
        if AUTOHIDE_MODE == "off":
            return
        if self.autohide_state in {"showing", "visible"}:
            return
        if AUTOHIDE_MODE == "snap":
            self.hide_offset = 0.0
            self.autohide_state = "visible"
            self.queue_redraw(reason="autohide.snap-show")
            return
        self.start_animation(target_visible=True)

    def trigger_hide(self) -> None:
        if AUTOHIDE_MODE == "off":
            return
        if self.autohide_state in {"hiding", "hidden"}:
            return
        if AUTOHIDE_MODE == "snap":
            self.hide_offset = 1.0
            self.autohide_state = "hidden"
            self.queue_redraw(reason="autohide.snap-hide")
            return
        self.start_animation(target_visible=False)

    def start_animation(self, *, target_visible: bool) -> None:
        if self.anim_id:
            GLib.source_remove(self.anim_id)
            self.anim_id = 0
        self.autohide_state = "showing" if target_visible else "hiding"
        start_offset = self.hide_offset
        end_offset = 0.0 if target_visible else 1.0

        def tick() -> bool:
            self.anim_progress = min(
                1.0, self.anim_progress + (FRAME_INTERVAL_MS / 220.0)
            )
            self.hide_offset = start_offset + (
                (end_offset - start_offset) * self.anim_progress
            )
            if self.anim_progress >= 1.0:
                self.hide_offset = end_offset
                self.autohide_state = "visible" if target_visible else "hidden"
                self.anim_progress = 0.0
                self.anim_id = 0
                self.queue_redraw(
                    reason="autohide.animate-finish-show"
                    if target_visible
                    else "autohide.animate-finish-hide"
                )
                return False
            self.queue_redraw(reason="autohide.animate")
            return True

        self.anim_progress = 0.0
        self.anim_id = GLib.timeout_add(FRAME_INTERVAL_MS, tick)

    @staticmethod
    def rounded_rect(
        cr: cairo.Context, x: float, y: float, w: float, h: float, r: float
    ) -> None:
        r = max(0.0, min(r, w / 2.0, h / 2.0))
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2.0, 0.0)
        cr.arc(x + w - r, y + h - r, r, 0.0, math.pi / 2.0)
        cr.arc(x + r, y + h - r, r, math.pi / 2.0, math.pi)
        cr.arc(x + r, y + r, r, math.pi, math.pi * 1.5)
        cr.close_path()


def main() -> int:
    display = Gdk.Display.get_default()
    if display is None:
        print("No GDK display available", file=sys.stderr)
        return 1
    print(
        "repro-start:"
        f" session_type={os.environ.get('XDG_SESSION_TYPE', '-')}"
        f" backend={display.__class__.__module__}.{display.__class__.__name__}"
        f" xwayland={is_xwayland_session(display)}"
        f" autohide={AUTOHIDE_MODE}"
        f" tick_pump={TICK_PUMP}"
        f" recover={RECOVER}",
        f" dock_hint={DOCK_HINT}",
        f" keep_above={KEEP_ABOVE}",
        f" sticky={STICKY}",
        f" rgba={USE_RGBA}",
        f" centered={CENTERED}",
        f" motion_spam={MOTION_SPAM}",
        f" blur_hint={BLUR_HINT}",
        flush=True,
    )
    window = ReproWindow()
    window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
