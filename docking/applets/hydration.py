"""Hydration reminder applet -- water drop that drains over time."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Callable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from docking.applets.base import Applet, draw_icon_label
from docking.applets.identity import AppletId

if TYPE_CHECKING:
    from docking.core.config import Config

TWO_PI = 2 * math.pi
DEFAULT_INTERVAL = 45
_INTERVAL_PRESETS = (15, 30, 45, 60, 90)
# Refresh icon every N ticks (seconds) when timer overlay is hidden
_REDRAW_EVERY = 10


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


def water_color() -> tuple[float, float, float]:
    """Return the water color (constant vivid blue)."""
    return (0.2, 0.5, 1.0)


def format_remaining(fill: float, interval_min: int) -> str:
    """Format remaining time as M:SS."""
    remaining = int(fill * interval_min * 60)
    m = remaining // 60
    s = remaining % 60
    return f"{m}:{s:02d}"


def tooltip_text(fill: float, interval_min: int) -> str:
    """Build tooltip string."""
    if fill <= 0:
        return "Drink water!"
    return f"Next in {format_remaining(fill=fill, interval_min=interval_min)}"


def mouth_curvature(fill: float) -> float:
    """Map fill level to mouth mood in [-1, 1].

    1.0 = full smile, 0.0 = neutral, -1.0 = full frown.
    """
    clamped = max(0.0, min(1.0, fill))
    return clamped * 2.0 - 1.0


# ---------------------------------------------------------------------------
# Cairo rendering
# ---------------------------------------------------------------------------


def _draw_drop_path(cr: cairo.Context, size: int) -> None:
    """Draw a teardrop/water drop path centered in size x size."""
    cx = size / 2
    tip_y = size * 0.08
    bot_y = size * 0.92
    # Widest point of the drop
    bulge_y = size * 0.64
    bulge_w = size * 0.38

    cr.new_path()
    cr.move_to(cx, tip_y)
    # Left side: tip down to bulge, curving outward
    cr.curve_to(
        cx - bulge_w * 0.3,
        size * 0.30,
        cx - bulge_w,
        bulge_y - size * 0.15,
        cx - bulge_w,
        bulge_y,
    )
    # Bottom: two curves through a single low point for a rounder belly.
    cr.curve_to(
        cx - bulge_w * 1.05,
        bulge_y + size * 0.18,
        cx - bulge_w * 0.45,
        bot_y,
        cx,
        bot_y,
    )
    cr.curve_to(
        cx + bulge_w * 0.45,
        bot_y,
        cx + bulge_w * 1.05,
        bulge_y + size * 0.18,
        cx + bulge_w,
        bulge_y,
    )
    # Right side: bulge back up to tip
    cr.curve_to(
        cx + bulge_w,
        bulge_y - size * 0.15,
        cx + bulge_w * 0.3,
        size * 0.30,
        cx,
        tip_y,
    )
    cr.close_path()


def _render_drop(cr: cairo.Context, size: int, fill: float) -> None:
    """Render a water drop with fill level."""
    r, g, b = water_color()

    # Solid dark background so the drop shape is always visible
    _draw_drop_path(cr=cr, size=size)
    cr.set_source_rgb(0.12, 0.12, 0.18)
    cr.fill()

    # Water fill: clip to drop shape, draw rect from bottom.
    # Drop spans ~0.08 (tip) to ~0.92 (bottom). Map fill 1.0->tip, 0.0->bottom.
    if fill > 0:
        cr.save()
        _draw_drop_path(cr=cr, size=size)
        cr.clip()
        drop_top = size * 0.08
        drop_bot = size * 0.92
        fill_top = drop_bot - fill * (drop_bot - drop_top)
        cr.rectangle(0, fill_top, size, size - fill_top)
        cr.set_source_rgb(r, g, b)
        cr.fill()
        cr.restore()

    # Thick white outline so the shape pops on any background
    _draw_drop_path(cr=cr, size=size)
    cr.set_source_rgba(1, 1, 1, 0.9)
    cr.set_line_width(max(1.3, size * 0.035))
    cr.stroke()

    # Small highlight on upper-left
    cr.save()
    hx = size * 0.38
    hy = size * 0.45
    cr.translate(hx, hy)
    cr.scale(size * 0.08, size * 0.12)
    cr.arc(0, 0, 1.0, 0, TWO_PI)
    cr.restore()
    cr.set_source_rgba(1, 1, 1, 0.3 * max(0.0, fill))
    cr.fill()

    # Face styled like Pomodoro (dark eyes + arc mouth). Mouth transitions
    # from smile -> neutral -> frown as water drains.
    cr.save()
    _draw_drop_path(cr=cr, size=size)
    cr.clip()

    cx = size / 2
    cy = size * 0.54
    eye_r = size * 0.04
    eye_y = cy - size * 0.05
    eye_dx = size * 0.11

    # Eyes
    cr.set_source_rgba(0.12, 0.12, 0.16, 0.95)
    cr.arc(cx - eye_dx, eye_y, eye_r, 0, TWO_PI)
    cr.fill()
    cr.arc(cx + eye_dx, eye_y, eye_r, 0, TWO_PI)
    cr.fill()

    # Mouth (Pomodoro-like arc; flips as fill decreases)
    mouth_y = cy + size * 0.04
    mood = mouth_curvature(fill=fill)
    strength = abs(mood)

    # Near neutral, draw a short flat line.
    if strength < 0.08:
        half_w = size * 0.09
        cr.set_source_rgba(0.12, 0.12, 0.16, 0.95)
        cr.set_line_width(max(1.0, size * 0.03))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.move_to(cx - half_w, mouth_y)
        cr.line_to(cx + half_w, mouth_y)
        cr.stroke()
        cr.restore()
        return

    smile_r = size * (0.04 + 0.06 * strength)
    cr.set_source_rgba(0.12, 0.12, 0.16, 0.95)
    cr.set_line_width(max(1.0, size * 0.03))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)

    if mood >= 0:
        # Smile arc (happy/full).
        cr.arc(cx, mouth_y, smile_r, 0.2, math.pi - 0.2)
    else:
        # Frown arc (sad/empty).
        cr.arc(cx, mouth_y + size * 0.03, smile_r, math.pi + 0.2, TWO_PI - 0.2)
    cr.stroke()
    cr.restore()


# ---------------------------------------------------------------------------
# Applet
# ---------------------------------------------------------------------------


class HydrationApplet(Applet):
    """Reminds you to drink water at configurable intervals.

    The water drop icon drains over time. Click to refill (log a drink).
    Triggers urgent bounce when fully drained.
    """

    id = AppletId.HYDRATION
    name = "Hydration"
    icon_name = "weather-showers"

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._fill = 1.0
        self._interval_min = DEFAULT_INTERVAL
        self._show_timer = False
        self._timer_id: int = 0
        self._tick_count = 0

        if config:
            prefs = config.applet_prefs.get("hydration", {})
            self._interval_min = prefs.get("interval", DEFAULT_INTERVAL)
            self._show_timer = prefs.get("show_timer", False)

        super().__init__(icon_size, config)
        self._update_tooltip()

    def _update_tooltip(self) -> None:
        self.item.name = tooltip_text(fill=self._fill, interval_min=self._interval_min)

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        _render_drop(cr=cr, size=size, fill=self._fill)
        if self._show_timer and self._fill > 0:
            text = format_remaining(fill=self._fill, interval_min=self._interval_min)
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

    def on_clicked(self) -> None:
        """Refill - user drank water."""
        self._fill = 1.0
        self.item.is_urgent = False
        self._tick_count = 0
        self._update_tooltip()
        self.refresh_icon()

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        items: list[Gtk.MenuItem] = []

        show = Gtk.CheckMenuItem(label="Show Timer")
        show.set_active(self._show_timer)
        show.connect("toggled", self._on_toggle_timer)
        items.append(show)

        items.append(Gtk.SeparatorMenuItem())

        for mins in _INTERVAL_PRESETS:
            mi = Gtk.CheckMenuItem(label=f"{mins} min")
            mi.set_active(self._interval_min == mins)
            mi.connect(
                "toggled",
                lambda _w, m=mins: self._set_interval(minutes=m),
            )
            items.append(mi)
        return items

    def _on_toggle_timer(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_timer = widget.get_active()
        self._save()
        self.refresh_icon()

    def _tick(self) -> bool:
        if self._fill <= 0:
            return True
        total_secs = self._interval_min * 60
        self._fill = max(0.0, self._fill - 1.0 / total_secs)
        self._tick_count += 1

        if self._fill <= 0:
            self.item.is_urgent = True
            self.item.last_urgent = GLib.get_monotonic_time()
            self._update_tooltip()
            self.refresh_icon()
        elif self._show_timer or self._tick_count % _REDRAW_EVERY == 0:
            self._update_tooltip()
            self.refresh_icon()

        return True

    def _set_interval(self, minutes: int) -> None:
        self._interval_min = minutes
        self._save()

    def _save(self) -> None:
        self.save_prefs(
            prefs={"interval": self._interval_min, "show_timer": self._show_timer}
        )
