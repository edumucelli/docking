"""Poof smoke animation — shown when an icon is removed by dragging off the dock."""

from __future__ import annotations

import cairo
from pathlib import Path

from docking.log import get_logger

log = get_logger("poof")

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf, GLib  # noqa: E402

POOF_DURATION_MS = 300

_poof_pixbuf: GdkPixbuf.Pixbuf | None = None
_poof_loaded = False


def _load_poof() -> GdkPixbuf.Pixbuf | None:
    global _poof_pixbuf, _poof_loaded
    if _poof_loaded:
        return _poof_pixbuf
    _poof_loaded = True
    svg_path = str(Path(__file__).parent.parent / "assets" / "poof.svg")
    try:
        _poof_pixbuf = GdkPixbuf.Pixbuf.new_from_file(svg_path)
    except (GLib.Error, FileNotFoundError):
        log.warning("poof.svg not found at %s", svg_path)
    return _poof_pixbuf


def show_poof(x: int, y: int) -> None:
    """Show Plank's poof sprite-sheet animation at (x, y) screen coords."""
    pixbuf = _load_poof()
    if pixbuf is None:
        return

    frame_size = pixbuf.get_width()
    num_frames = pixbuf.get_height() // frame_size
    if num_frames < 1:
        return

    win = Gtk.Window(type=Gtk.WindowType.POPUP)
    win.set_decorated(False)
    win.set_skip_taskbar_hint(True)
    win.set_app_paintable(True)
    win.set_size_request(frame_size, frame_size)

    screen = win.get_screen()
    visual = screen.get_rgba_visual()
    if visual:
        win.set_visual(visual)

    # Store animation state on the window to prevent GC issues
    win.poof = {
        "frame": 0,
        "pixbuf": pixbuf,
        "frame_size": frame_size,
        "num_frames": num_frames,
    }

    def on_draw(widget: Gtk.Widget, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        p = widget.poof
        f = min(p["frame"], p["num_frames"] - 1)
        Gdk.cairo_set_source_pixbuf(cr, p["pixbuf"], 0, -p["frame_size"] * f)
        cr.rectangle(0, 0, p["frame_size"], p["frame_size"])
        cr.fill()
        return True

    def tick(w: Gtk.Window) -> bool:
        p = w.poof
        p["frame"] += 1
        log.debug("tick: frame=%d/%d", p["frame"], p["num_frames"])
        if p["frame"] >= p["num_frames"]:
            w.destroy()
            return False
        w.queue_draw()
        return True

    win.connect("draw", on_draw)
    win.move(x - frame_size // 2, y - frame_size // 2)
    win.show_all()
    log.debug(
        "shown at (%d,%d) frames=%d interval=%dms",
        x,
        y,
        num_frames,
        POOF_DURATION_MS // num_frames,
    )
    GLib.timeout_add(POOF_DURATION_MS // num_frames, tick, win)
