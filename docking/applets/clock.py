"""Clock applet -- analog or digital time display as a dock icon.

Three rendering modes controlled by preferences:
  1. Analog 12-hour (default) -- SVG face layers + Cairo hands
  2. Analog 24-hour           -- 24h SVG theme, hour hand covers full day
  3. Digital                  -- outlined text, optional date and AM/PM
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from docking.applets.base import Applet

if TYPE_CHECKING:
    from docking.core.config import Config

_CLOCK_THEMES_DIR = Path(__file__).parent.parent / "assets" / "clock"

# SVG layers composited bottom-to-top for the analog face
_FACE_LAYERS = [
    "clock-drop-shadow",
    "clock-face-shadow",
    "clock-face",
    "clock-marks",
]
_TOP_LAYERS = [
    "clock-glass",
    "clock-frame",
]


def minute_rotation(minute: int) -> float:
    """Rotation angle (radians) for the minute hand."""
    return math.pi * (minute / 30.0 + 1.0)


def hour_rotation_12h(hour: int, minute: int) -> float:
    """Rotation angle (radians) for the hour hand in 12-hour mode."""
    return math.pi * (hour % 12 / 6.0 + minute / 360.0 + 1.0)


def hour_rotation_24h(hour: int, minute: int) -> float:
    """Rotation angle (radians) for the hour hand in 24-hour mode."""
    return math.pi * (hour % 24 / 12.0 + minute / 720.0 + 1.0)


class ClockApplet(Applet):
    """Displays current time as an analog clock face or digital readout."""

    id = "clock"
    name = "Clock"
    icon_name = "clock"

    _DEFAULT_PREFS: dict[str, Any] = {
        "show_digital": False,
        "show_military": False,
        "show_date": False,
    }

    def __init__(self, icon_size: int, config: Config | None = None) -> None:
        self._timer = _MinuteTimer()

        # Load prefs before super().__init__ which calls create_icon
        self._show_digital = False
        self._show_military = False
        self._show_date = False
        if config:
            prefs = config.applet_prefs.get("clock", {})
            self._show_digital = prefs.get("show_digital", False)
            self._show_military = prefs.get("show_military", False)
            self._show_date = prefs.get("show_date", False)

        super().__init__(icon_size, config)

    # -- Icon rendering ------------------------------------------------------

    def create_icon(self, size: int) -> GdkPixbuf.Pixbuf | None:
        """Render clock icon in current mode."""
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        now = time.localtime()
        is_24h = self._show_military

        if self._show_digital:
            self._render_digital(
                cr=cr, size=size, now=now, is_24h=is_24h, show_date=self._show_date
            )
        else:
            self._render_analog(cr=cr, size=size, now=now)

        # Update tooltip with full date+time (guard: item not yet set on first call)
        if hasattr(self, "item"):
            if is_24h:
                self.item.name = time.strftime("%a, %b %-d %H:%M", now)
            else:
                self.item.name = time.strftime("%a, %b %-d %-I:%M %p", now)

        return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)

    def _render_analog(
        self,
        cr: cairo.Context,
        size: int,
        now: time.struct_time,
    ) -> None:
        """Draw analog clock: SVG face layers, Cairo hands, SVG glass+frame.

        Always uses 12-hour face; 24h pref only affects tooltip/digital.
        """
        center = size / 2
        radius = center
        theme_dir = _CLOCK_THEMES_DIR / "Default"

        # Bottom SVG layers: shadow, face, marks
        for name in _FACE_LAYERS:
            _paint_svg(cr=cr, path=theme_dir / f"{name}.svg", size=size)

        # Hands (drawn between face and glass/frame layers)
        lw = max(1.0, size / 48.0)
        minute = now.tm_min
        hour = now.tm_hour

        cr.translate(center, center)
        cr.set_line_width(lw)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        # Minute hand (dark gray, longer)
        cr.save()
        cr.set_source_rgba(0.15, 0.15, 0.15, 1)
        cr.rotate(minute_rotation(minute=minute))
        cr.move_to(0, radius - radius * 0.35)
        cr.line_to(0, -radius * 0.15)
        cr.stroke()
        cr.restore()

        # Hour hand (black, shorter)
        cr.save()
        cr.set_source_rgba(0, 0, 0, 1)
        cr.rotate(hour_rotation_12h(hour=hour, minute=minute))
        cr.move_to(0, radius - radius * 0.5)
        cr.line_to(0, -radius * 0.15)
        cr.stroke()
        cr.restore()

        cr.translate(-center, -center)

        # Top SVG layers: glass highlight, frame bezel
        for name in _TOP_LAYERS:
            _paint_svg(cr=cr, path=theme_dir / f"{name}.svg", size=size)

    def _render_digital(
        self,
        cr: cairo.Context,
        size: int,
        now: time.struct_time,
        is_24h: bool,
        show_date: bool,
    ) -> None:
        """Draw outlined digital time (and optionally date + AM/PM).

        All text is rendered with a black outline and white fill so it's
        readable against any background or theme.
        """
        center = size / 2

        # Build list of (text, font_desc, stroke_width, rgba) rows to draw
        rows: list[tuple[str, Pango.FontDescription, float, tuple[float, ...]]] = []

        # Time text
        if is_24h:
            time_str = time.strftime("%H:%M", now)
        else:
            time_str = time.strftime("%l:%M", now).strip()
        time_font_size = max(1, int(size / 4))
        time_font = Pango.FontDescription(f"Sans Bold {time_font_size}px")
        rows.append((time_str, time_font, 3.0, (1, 1, 1, 1)))

        # Date text (digital mode only)
        if show_date:
            date_str = time.strftime("%b %-d", now)
            date_font_size = max(1, int(size / 5))
            date_font = Pango.FontDescription(f"Sans Bold {date_font_size}px")
            rows.append((date_str, date_font, 2.5, (1, 1, 1, 1)))

        # Measure all rows to compute vertical layout
        layouts = []
        total_h = 0
        spacing = max(1, int(size * 0.04))
        for text, font, _sw, _rgba in rows:
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(font)
            layout.set_text(text, -1)
            _ink, logical = layout.get_pixel_extents()
            layouts.append((layout, logical))
            total_h += logical.height

        # AM/PM indicator (12h mode only, below time)
        am_pm_height = 0
        am_pm_font_size = max(1, int(size / 5))
        am_pm_font = Pango.FontDescription(f"Sans Bold {am_pm_font_size}px")
        if not is_24h:
            tmp_layout = PangoCairo.create_layout(cr)
            tmp_layout.set_font_description(am_pm_font)
            tmp_layout.set_text("AM", -1)
            _ink, am_logical = tmp_layout.get_pixel_extents()
            am_pm_height = am_logical.height
            total_h += am_pm_height

        num_gaps = len(rows) - 1 + (1 if not is_24h else 0)
        total_h += num_gaps * spacing

        # Draw rows centered vertically
        y = center - total_h / 2
        for idx, (text, font, stroke_w, rgba) in enumerate(rows):
            layout, logical = layouts[idx]
            tx = center - logical.width / 2 - logical.x
            self._draw_outlined_text(
                cr=cr,
                layout=layout,
                x=tx,
                y=y - logical.y,
                stroke_width=stroke_w,
                fill_rgba=rgba,
            )
            y += logical.height + spacing

            # AM/PM row right after time (first row)
            if idx == 0 and not is_24h:
                is_pm = now.tm_hour >= 12
                self._draw_am_pm(cr=cr, size=size, y=y, font=am_pm_font, is_pm=is_pm)
                y += am_pm_height + spacing

    def _draw_am_pm(
        self,
        cr: cairo.Context,
        size: int,
        y: float,
        font: Pango.FontDescription,
        is_pm: bool,
    ) -> None:
        """Draw AM/PM indicators: active one bright, inactive dim."""
        center = size / 2
        quarter = size / 4
        for label, active, x_center in [
            ("AM", not is_pm, quarter),
            ("PM", is_pm, center + quarter),
        ]:
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(font)
            layout.set_text(label, -1)
            _ink, logical = layout.get_pixel_extents()
            tx = x_center - logical.width / 2 - logical.x
            alpha = 1.0 if active else 0.35
            self._draw_outlined_text(
                cr=cr,
                layout=layout,
                x=tx,
                y=y - logical.y,
                stroke_width=2.5,
                fill_rgba=(1, 1, 1, alpha),
            )

    @staticmethod
    def _draw_outlined_text(
        cr: cairo.Context,
        layout: Pango.Layout,
        x: float,
        y: float,
        stroke_width: float,
        fill_rgba: tuple[float, ...] = (1, 1, 1, 1),
    ) -> None:
        """Draw Pango text with a black outline and colored fill."""
        cr.save()
        cr.move_to(x, y)
        PangoCairo.layout_path(cr, layout)
        cr.set_source_rgba(0, 0, 0, 1)
        cr.set_line_width(stroke_width)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke_preserve()
        cr.set_source_rgba(*fill_rgba)
        cr.fill()
        cr.restore()

    # -- Menu items ----------------------------------------------------------

    def get_menu_items(self) -> list[Gtk.MenuItem]:
        """Three toggles: Digital Clock, 24-Hour Clock, Show Date."""
        items: list[Gtk.MenuItem] = []

        digital = Gtk.CheckMenuItem(label="Digital Clock")
        digital.set_active(self._show_digital)
        digital.connect("toggled", self._on_toggle_digital)
        items.append(digital)

        military = Gtk.CheckMenuItem(label="24-Hour Clock")
        military.set_active(self._show_military)
        military.connect("toggled", self._on_toggle_military)
        items.append(military)

        date = Gtk.CheckMenuItem(label="Show Date")
        date.set_active(self._show_date)
        date.set_sensitive(self._show_digital)
        date.connect("toggled", self._on_toggle_date)
        items.append(date)

        return items

    def _on_toggle_digital(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_digital = widget.get_active()
        self._save_prefs()
        self.refresh_icon()

    def _on_toggle_military(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_military = widget.get_active()
        self._save_prefs()
        self.refresh_icon()

    def _on_toggle_date(self, widget: Gtk.CheckMenuItem) -> None:
        self._show_date = widget.get_active()
        self._save_prefs()
        self.refresh_icon()

    def _save_prefs(self) -> None:
        self.save_prefs(
            prefs={
                "show_digital": self._show_digital,
                "show_military": self._show_military,
                "show_date": self._show_date,
            }
        )

    def start(self, notify: Callable[[], None]) -> None:
        super().start(notify)
        self._timer.start(self.refresh_icon)

    def stop(self) -> None:
        self._timer.stop()
        super().stop()


class _MinuteTimer:
    """1-second GLib timer that fires a callback once per minute change."""

    def __init__(self) -> None:
        self._timer_id: int = 0
        self._last_minute: int = -1
        self._callback: Callable[[], None] | None = None

    def start(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        self._timer_id = GLib.timeout_add_seconds(1, self._tick)

    def stop(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self._callback = None

    def _tick(self) -> bool:
        now = time.localtime()
        if now.tm_min != self._last_minute:
            self._last_minute = now.tm_min
            if self._callback:
                self._callback()
        return True


def _paint_svg(cr: cairo.Context, path: Path, size: int) -> None:
    """Load an SVG at the given size and paint it onto the Cairo context."""
    pbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(str(path), size, size)
    Gdk.cairo_set_source_pixbuf(cr, pbuf, 0, 0)
    cr.paint()
