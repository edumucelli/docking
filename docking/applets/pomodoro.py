"""Pomodoro applet -- flat tomato icon with countdown overlay."""

from __future__ import annotations

import math
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from docking.applets.base import Applet, draw_icon_label
from docking.log import get_logger

if TYPE_CHECKING:
    from docking.core.config import Config

_log = get_logger(name="pomodoro")

TWO_PI = 2 * math.pi

# Default durations in minutes
DEFAULT_WORK = 25
DEFAULT_BREAK = 5
DEFAULT_LONG_BREAK = 15
LONG_BREAK_EVERY = 4


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class State(Enum):
    IDLE = "idle"
    WORK = "work"
    BREAK = "break"
    LONG_BREAK = "long_break"
    PAUSED = "paused"


# RGB tuples per state (tomato body color)
_STATE_COLORS: dict[State, tuple[float, float, float]] = {
    State.IDLE: (0.85, 0.16, 0.12),
    State.WORK: (0.85, 0.16, 0.12),
    State.BREAK: (0.25, 0.72, 0.35),
    State.LONG_BREAK: (0.30, 0.55, 0.85),
    State.PAUSED: (0.85, 0.16, 0.12),
}


def format_time(seconds: int) -> str:
    """Format seconds as MM:SS."""
    m = seconds // 60
    s = seconds % 60
    return f"{m:02d}:{s:02d}"


def tooltip_text(state: State, remaining: int) -> str:
    """Build tooltip string for given state."""
    if state == State.IDLE:
        return "Pomodoro"
    if state == State.PAUSED:
        return f"Paused - {format_time(seconds=remaining)}"
    labels = {State.WORK: "Work", State.BREAK: "Break", State.LONG_BREAK: "Long Break"}
    return f"{labels[state]}: {format_time(seconds=remaining)} remaining"


# ---------------------------------------------------------------------------
# Cairo rendering
# ---------------------------------------------------------------------------


def _draw_tomato(
    cr: cairo.Context, size: int, r: float, g: float, b: float, alpha: float
) -> None:
    """Draw a flat tomato: red ellipse body + green stem/leaf."""
    cx = size / 2
    # Shift body down slightly to leave room for stem
    cy = size * 0.55
    rx = size * 0.40  # horizontal radius
    ry = size * 0.36  # vertical radius (squatter)

    # Body
    cr.save()
    cr.translate(cx, cy)
    cr.scale(rx, ry)
    cr.arc(0, 0, 1.0, 0, TWO_PI)
    cr.restore()
    cr.set_source_rgba(r, g, b, alpha)
    cr.fill()

    # Subtle highlight (lighter ellipse in upper-left)
    cr.save()
    cr.translate(cx - rx * 0.25, cy - ry * 0.30)
    cr.scale(rx * 0.45, ry * 0.35)
    cr.arc(0, 0, 1.0, 0, TWO_PI)
    cr.restore()
    cr.set_source_rgba(1, 1, 1, 0.18 * alpha)
    cr.fill()

    # Stem (small green rectangle)
    stem_w = size * 0.06
    stem_h = size * 0.12
    stem_x = cx - stem_w / 2
    stem_y = cy - ry - stem_h * 0.5
    cr.rectangle(stem_x, stem_y, stem_w, stem_h)
    cr.set_source_rgba(0.25, 0.55, 0.20, alpha)
    cr.fill()

    # Leaf (small green ellipse tilted right)
    leaf_cx = cx + size * 0.06
    leaf_cy = cy - ry - stem_h * 0.15
    cr.save()
    cr.translate(leaf_cx, leaf_cy)
    cr.rotate(0.5)  # slight tilt
    cr.scale(size * 0.10, size * 0.05)
    cr.arc(0, 0, 1.0, 0, TWO_PI)
    cr.restore()
    cr.set_source_rgba(0.30, 0.65, 0.25, alpha)
    cr.fill()


def _draw_face(cr: cairo.Context, size: int) -> None:
    """Draw a cute face on the tomato (IDLE state only)."""
    cx = size / 2
    cy = size * 0.52
    eye_r = size * 0.04
    eye_y = cy - size * 0.04
    eye_offset = size * 0.12

    # Eyes
    cr.set_source_rgba(0.15, 0.15, 0.15, 1)
    cr.arc(cx - eye_offset, eye_y, eye_r, 0, TWO_PI)
    cr.fill()
    cr.arc(cx + eye_offset, eye_y, eye_r, 0, TWO_PI)
    cr.fill()

    # Smile (arc)
    smile_r = size * 0.10
    smile_y = cy + size * 0.04
    cr.set_line_width(max(1.0, size * 0.03))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.arc(cx, smile_y, smile_r, 0.2, math.pi - 0.2)
    cr.stroke()


# ---------------------------------------------------------------------------
# Applet
# ---------------------------------------------------------------------------

# Duration presets for menu radio groups
_WORK_PRESETS = (15, 25, 30, 45)
_BREAK_PRESETS = (5, 10)
_LONG_BREAK_PRESETS = (15, 20, 30)


class PomodoroApplet(Applet):
    """Pomodoro timer with flat tomato icon.

    Left-click starts/pauses. Auto-cycles work/break phases.
    Right-click menu offers reset and duration presets.
    """

    id = "pomodoro"
    name = "Pomodoro"
    icon_name = "alarm"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._state = State.IDLE
        self._paused_from: State = State.WORK
        self._remaining = 0
        self._work_count = 0
        self._timer_id: int = 0

        # Load preferences
        self._work_min = DEFAULT_WORK
        self._break_min = DEFAULT_BREAK
        self._long_break_min = DEFAULT_LONG_BREAK
        self._show_timer = True
        if config:
            prefs = config.applet_prefs.get("pomodoro", {})
            self._work_min = prefs.get("work", DEFAULT_WORK)
            self._break_min = prefs.get("break_", DEFAULT_BREAK)
            self._long_break_min = prefs.get("long_break", DEFAULT_LONG_BREAK)
            self._show_timer = prefs.get("show_timer", True)

        super().__init__(icon_size, config)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        self.item.name = tooltip_text(state=self._state, remaining=self._remaining)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)

        state = self._paused_from if self._state == State.PAUSED else self._state
        r, g, b = _STATE_COLORS.get(state, (0.85, 0.16, 0.12))
        alpha = 0.5 if self._state == State.PAUSED else 1.0

        _draw_tomato(cr=cr, size=size, r=r, g=g, b=b, alpha=alpha)

        if self._state == State.IDLE:
            _draw_face(cr=cr, size=size)
        elif self._show_timer:
            text = format_time(seconds=self._remaining)
            draw_icon_label(cr=cr, text=text, size=size)

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        super().stop()

    # -- Interaction ---------------------------------------------------------

    def on_clicked(self) -> None:
        """Start/pause toggle."""
        if self._state == State.IDLE:
            self._start_work()
        elif self._state == State.PAUSED:
            self._state = self._paused_from
        else:
            # Running → pause
            self._paused_from = self._state
            self._state = State.PAUSED
        self._update_tooltip()
        self.refresh_icon()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        # Reset
        reset = Gtk.MenuItem(label="Reset")
        reset.connect("activate", lambda _w: self._reset())
        items.append(reset)

        show = Gtk.CheckMenuItem(label="Show Timer")
        show.set_active(self._show_timer)
        show.connect("toggled", self._on_toggle_timer)
        items.append(show)

        items.append(Gtk.SeparatorMenuItem())

        # Work duration
        items.append(self._make_duration_header(label="Work"))
        for mins in _WORK_PRESETS:
            items.append(
                self._make_radio_item(
                    label=f"{mins} min",
                    active=self._work_min == mins,
                    callback=lambda _w, m=mins: self._set_work(minutes=m),
                )
            )

        items.append(Gtk.SeparatorMenuItem())

        # Break duration
        items.append(self._make_duration_header(label="Break"))
        for mins in _BREAK_PRESETS:
            items.append(
                self._make_radio_item(
                    label=f"{mins} min",
                    active=self._break_min == mins,
                    callback=lambda _w, m=mins: self._set_break(minutes=m),
                )
            )

        items.append(Gtk.SeparatorMenuItem())

        # Long break duration
        items.append(self._make_duration_header(label="Long Break"))
        for mins in _LONG_BREAK_PRESETS:
            items.append(
                self._make_radio_item(
                    label=f"{mins} min",
                    active=self._long_break_min == mins,
                    callback=lambda _w, m=mins: self._set_long_break(minutes=m),
                )
            )

        return items

    # -- Internals -----------------------------------------------------------

    def _tick(self) -> bool:
        if self._state in (State.IDLE, State.PAUSED):
            return True
        self._remaining -= 1
        if self._remaining <= 0:
            self._auto_transition()
        self._update_tooltip()
        self.refresh_icon()
        return True

    def _start_work(self) -> None:
        self._state = State.WORK
        self._remaining = self._work_min * 60

    def _auto_transition(self) -> None:
        """Transition to next phase when timer expires."""
        if self._state == State.WORK:
            self._work_count += 1
            if self._work_count % LONG_BREAK_EVERY == 0:
                self._state = State.LONG_BREAK
                self._remaining = self._long_break_min * 60
            else:
                self._state = State.BREAK
                self._remaining = self._break_min * 60
        elif self._state in (State.BREAK, State.LONG_BREAK):
            self._start_work()
        # Trigger urgent bounce+glow to notify phase change
        self.item.is_urgent = True
        self.item.last_urgent = GLib.get_monotonic_time()

    def _reset(self) -> None:
        self._state = State.IDLE
        self._remaining = 0
        self._work_count = 0
        self._update_tooltip()
        self.refresh_icon()

    def _save(self) -> None:
        self.save_prefs(
            prefs={
                "work": self._work_min,
                "break_": self._break_min,
                "long_break": self._long_break_min,
                "show_timer": self._show_timer,
            }
        )

    def _on_toggle_timer(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_timer = widget.get_active()
        self._save()
        self.refresh_icon()

    def _set_work(self, minutes: int) -> None:
        self._work_min = minutes
        self._save()

    def _set_break(self, minutes: int) -> None:
        self._break_min = minutes
        self._save()

    def _set_long_break(self, minutes: int) -> None:
        self._long_break_min = minutes
        self._save()

    @staticmethod
    def _make_duration_header(label: str) -> Gtk.MenuItem:
        mi = Gtk.MenuItem(label=label)
        mi.set_sensitive(False)
        return mi

    @staticmethod
    def _make_radio_item(label: str, active: bool, callback: Any) -> Gtk.CheckMenuItem:
        mi = Gtk.CheckMenuItem(label=label)
        mi.set_active(active)
        mi.connect("toggled", callback)
        return mi
