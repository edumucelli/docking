"""Preview-hover window outline on a real layer-shell compositor.

Shows the dock's real WindowOutline around a floating fixture window, then
checks with compositor screenshots that only the stroke band changed, that
pointer clicks and keyboard focus pass through it, and where it stacks against
the preview popup. Run through ``native_services.sh <compositor> outline``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import ClassVar

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk

PROBES = Path(__file__).resolve().parent
RECT = (200, 150, 600, 384)
STROKE = 4
# Mirrors docking.ui.window_outline.OUTLINE_RGBA.
RGBA = (0.39, 0.71, 1.0, 0.95)


def fixture():
    GLib.set_prgname("lab-native")
    log = Path(os.environ["LAB_DIR"]) / "fixture-events.jsonl"
    window = Gtk.Window(title="Outline fixture")
    window.set_decorated(False)
    window.set_default_size(RECT[2], RECT[3])
    area = Gtk.DrawingArea()
    area.add_events(0x4 | 0x100 | 0x200)  # pointer motion, button press, release

    def draw(_widget, cr):
        cr.set_source_rgb(1, 0, 0)
        cr.paint()
        return False

    def record(kind):
        def handler(_widget, event):
            with log.open("a") as handle:
                handle.write(json.dumps({"event": kind, "x": event.x, "y": event.y}))
                handle.write("\n")
            return False

        return handler

    area.connect("draw", draw)
    area.connect("button-press-event", record("press"))
    area.connect("motion-notify-event", record("motion"))
    window.add(area)
    window.connect("destroy", Gtk.main_quit)
    window.show_all()
    Gtk.main()


class Probe:
    buffers: ClassVar[dict] = {}

    def __init__(self, dock, backend):
        self.dock, self.backend = dock, backend
        self.evidence = Path(os.environ["LAB_DIR"])
        self.child = self.pointer_server = None
        self.socket = self.evidence / "outline-input.sock"
        self.checks = {}

    def settle(self, seconds=0.3):
        # Yield to GTK's main loop; iterating it recursively starves painting.
        yield seconds

    def wait(self, predicate, message):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if predicate():
                return
            yield 0.05
        raise AssertionError(message)

    def shot(self, name):
        yield from self.settle(0.5)
        target = self.evidence / f"outline-{name}.png"
        subprocess.run(["grim", "-s", "1", str(target)], check=True, timeout=10)
        return GdkPixbuf.Pixbuf.new_from_file(str(target))

    @staticmethod
    def pixel(image, x, y):
        # get_pixels() copies the whole buffer; keep one copy per image.
        if id(image) not in Probe.buffers:
            Probe.buffers[id(image)] = (image, image.get_pixels())
        data = Probe.buffers[id(image)][1]
        offset = y * image.get_rowstride() + x * image.get_n_channels()
        return tuple(data[offset : offset + 3])

    def click(self, x, y):
        rects = [o["rect"] for o in self.outputs()]
        width = max(r["x"] + r["width"] for r in rects)
        height = max(r["y"] + r["height"] for r in rects)
        subprocess.run(
            [
                sys.executable,
                str(PROBES / "input_probe.py"),
                str(self.socket),
                "--send",
                str(x),
                str(y),
                str(width),
                str(height),
                "272",
            ],
            check=True,
            timeout=10,
        )

    @staticmethod
    def outputs():
        return json.loads(
            subprocess.check_output(["swaymsg", "-t", "get_outputs", "-r"])
        )

    def events(self):
        path = self.evidence / "fixture-events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]

    def fixture_row(self):
        return next(
            (
                row
                for row in self.backend.windows.list_all_windows()
                if row.title == "Outline fixture" and row.geometry is not None
            ),
            None,
        )

    def run(self):
        outline = self.dock.preview._outline
        assert outline is not None, "dock did not create the outline overlay"
        self.dock.autohide.set_disabled(True, reason="outline probe")
        self.dock.autohide.set_hovered(False)
        self.child = subprocess.Popen([sys.executable, __file__, "--fixture"])
        yield from self.wait(self.fixture_row, "fixture not tracked with geometry")
        # Sway tiles by default; the outline needs an interior, floating rect.
        subprocess.run(
            [
                "swaymsg",
                (
                    '[app_id="lab-native"] floating enable, resize set width '
                    f"{RECT[2]} height {RECT[3]}, move position {RECT[0]} {RECT[1]}"
                ),
            ],
            check=True,
        )
        yield from self.wait(
            lambda: (row := self.fixture_row()) and row.geometry.width == RECT[2],
            "fixture did not float at the requested size",
        )
        yield from self.settle(0.5)
        row = self.fixture_row()
        geometry = row.geometry
        rect = (geometry.x, geometry.y, geometry.width, geometry.height)
        # `move position` is relative to the fixture's output, wherever it opened.
        assert rect[1:] == RECT[1:] and rect[0] % 1280 == RECT[0], ("geometry", rect)
        assert self.backend.windows.activate(row.id).succeeded

        yield from self.settle(0.5)
        before = yield from self.shot("before")
        outline.show_around(geometry)
        yield from self.wait(outline.get_mapped, "outline did not map")
        after = yield from self.shot("outline")
        self.check_pixels(before, after, rect)

        # Pointer path control, then click through the stroke on the left edge.
        with (self.evidence / "outline-input.log").open("w") as log:
            self.pointer_server = subprocess.Popen(
                [sys.executable, str(PROBES / "input_probe.py"), str(self.socket)],
                stderr=log,
            )
        yield from self.wait(self.socket.exists, "virtual pointer unavailable")
        mid_y = rect[1] + rect[3] // 2
        self.click(rect[0] + rect[2] // 2, mid_y)
        yield from self.wait(
            lambda: any(e["event"] == "press" for e in self.events()),
            "control click not delivered to the fixture",
        )
        presses = len([e for e in self.events() if e["event"] == "press"])
        self.click(rect[0] + 1, mid_y)
        yield from self.wait(
            lambda: len([e for e in self.events() if e["event"] == "press"]) > presses,
            "click on the outline stroke did not reach the window below",
        )
        landed = [e for e in self.events() if e["event"] == "press"][-1]
        self.checks["click_through"] = {"x": landed["x"], "y": landed["y"]}
        assert abs(landed["x"] - 1) < 2 and abs(landed["y"] - rect[3] // 2) < 2

        active = [r.id for r in self.backend.windows.list_all_windows() if r.active]
        self.checks["focus_kept"] = active == [row.id]
        assert self.checks["focus_kept"], ("focus moved", active)
        outline.hide()

        yield from self.preview_stacking(outline, rect, before)
        yield from self.second_output(outline, rect)
        yield from self.fullscreen_visibility(outline)
        from docking.platform.backends.wayland import services

        self.checks["layer"] = services.OVERLAY_LAYER
        (self.evidence / "outline-result.json").write_text(
            json.dumps(self.checks, indent=2)
        )
        print(json.dumps(self.checks), flush=True)

    def second_output(self, outline, rect):
        """Repeat the pixel check with the fixture on the other output."""
        outputs = self.outputs()
        if len(outputs) < 2:
            return
        current = next(
            o
            for o in outputs
            if o["rect"]["x"] <= rect[0] < o["rect"]["x"] + o["rect"]["width"]
        )
        other = next(o for o in outputs if o is not current)
        subprocess.run(
            [
                "swaymsg",
                (
                    '[app_id="lab-native"] move container to output '
                    f"{other['name']}, floating enable, "
                    f"move position {RECT[0]} {RECT[1]}"
                ),
            ],
            check=True,
        )
        yield from self.wait(
            lambda: (
                (row := self.fixture_row())
                and row.geometry.x == other["rect"]["x"] + RECT[0]
            ),
            "fixture did not move to the other output",
        )
        moved = self.fixture_row().geometry
        shifted = (moved.x, moved.y, moved.width, moved.height)
        outline.hide()
        yield from self.settle(0.5)
        before = yield from self.shot("second-output-before")
        outline.show_around(moved)
        yield from self.wait(outline.get_mapped, "outline did not map")
        after = yield from self.shot("second-output")
        self.check_pixels(before, after, shifted, key="second_output_pixels")
        outline.hide()

    def check_pixels(self, before, after, rect, key="pixels"):
        x, y, width, height = rect
        margin = 30
        band = outside = interior = 0
        wrong = []
        for py in range(y - margin, y + height + margin):
            for px in range(x - margin, x + width + margin):
                old, new = self.pixel(before, px, py), self.pixel(after, px, py)
                inside = x <= px < x + width and y <= py < y + height
                on_band = inside and (
                    px < x + STROKE
                    or px >= x + width - STROKE
                    or py < y + STROKE
                    or py >= y + height - STROKE
                )
                if on_band:
                    band += 1
                    want = tuple(
                        round(RGBA[3] * RGBA[i] * 255 + (1 - RGBA[3]) * old[i])
                        for i in range(3)
                    )
                    if max(abs(a - b) for a, b in zip(new, want, strict=True)) > 6:
                        wrong.append(("band", px, py, new, want))
                elif new != old:
                    wrong.append(("inside" if inside else "outside", px, py, old, new))
                    interior += inside
                    outside += not inside
        sample = {
            "top_left_corner": self.pixel(after, x, y),
            "left_edge_mid": self.pixel(after, x, y + height // 2),
            "just_outside_left": self.pixel(after, x - 1, y + height // 2),
            "just_inside_stroke": self.pixel(after, x + STROKE, y + height // 2),
        }
        self.checks[key] = {
            "band_pixels": band,
            "mismatches": len(wrong),
            "first_mismatches": wrong[:5],
            "sample": sample,
        }
        assert band == width * height - (width - 2 * STROKE) * (height - 2 * STROKE)
        assert not wrong, wrong[:5]

    def preview_stacking(self, outline, rect, before):
        """Compare the popup's pixels with and without the outline on top."""
        row = self.fixture_row()
        preview = self.dock.preview
        outputs = self.outputs()
        if len(outputs) > 1:
            self.checks["preview_stacking"] = "skipped: needs a single output"
            return
        output = outputs[0]["rect"]
        frame = self.dock.geometry.build_frame()
        icon = frame.static_dock_rect
        # The dock window is bottom-anchored and spans the output.
        preview.show_for_item(
            "lab-native.desktop",
            icon.x + icon.w / 2 - 24,
            48,
            output["height"] - frame.window_rect.h + icon.y,
        )
        yield from self.wait(preview.get_mapped, "preview popup did not map")
        popup_only = yield from self.shot("popup")
        preview._on_thumb_enter(None, None, row)
        yield from self.wait(outline.get_mapped, "outline did not map over popup")
        both = yield from self.shot("popup-outline")
        stroke_bottom = rect[1] + rect[3] - STROKE
        covered = outlined = 0
        for py in range(stroke_bottom, rect[1] + rect[3]):
            for px in range(rect[0], rect[0] + rect[2]):
                old, new = self.pixel(popup_only, px, py), self.pixel(both, px, py)
                if old != self.pixel(before, px, py):
                    covered += 1
                    outlined += old != new
        self.checks["preview_stacking"] = {
            "popup_pixels_on_bottom_stroke": covered,
            "overwritten_by_outline": outlined,
        }

    def fullscreen_visibility(self, outline):
        """Layer evidence: a fullscreen window covers TOP but not OVERLAY."""
        outline.hide()
        preview = self.dock.preview
        preview._do_hide()
        subprocess.run(
            ["swaymsg", '[app_id="lab-native"] fullscreen enable'], check=True
        )
        yield from self.wait(
            lambda: (row := self.fixture_row()) and row.fullscreen,
            "fixture did not go fullscreen",
        )
        row = self.fixture_row()
        outline.show_around(row.geometry)
        yield from self.wait(outline.get_mapped, "outline did not map")
        image = yield from self.shot("fullscreen")
        pixel = self.pixel(image, row.geometry.x + 1, row.geometry.y + 100)
        self.checks["outline_above_fullscreen"] = pixel != (255, 0, 0)
        self.checks["fullscreen_edge_pixel"] = pixel

    def close(self):
        for process in (self.child, self.pointer_server):
            if process is not None:
                process.terminate()
                process.wait(timeout=3)


def main():
    if "--fixture" in sys.argv:
        fixture()
        return
    from docking.launcher import prepare_display

    prepare_display()
    from docking import app
    from docking.core.config import Config, PinnedEntry

    applications = Path(os.environ["XDG_DATA_HOME"]) / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    (applications / "lab-native.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=Native fixture\nExec=/usr/bin/true\n"
        "StartupWMClass=lab-native\nIcon=application-x-executable\n"
    )
    Config(
        pinned=[PinnedEntry("app", "lab-native.desktop")],
        position="bottom",
        hide_mode="dodge-active",
        hide_delay_ms=0,
        unhide_delay_ms=0,
        zoom_enabled=False,
        startup_tips_enabled=False,
        update_check_enabled=False,
        window_preview_outline_window_on_hover=True,
    ).save()
    original = app._start_runtime
    failures = []

    def start(items, model, backend):
        original(items, model, backend)
        probe = Probe(items._window, backend)
        task = probe.run()

        def run():
            try:
                delay = next(task)
                GLib.timeout_add(max(1, round(delay * 1000)), run)
                return False
            except StopIteration:
                pass
            except Exception:
                failures.append(traceback.format_exc())
                print(failures[-1], flush=True)
            probe.close()
            Gtk.main_quit()
            return False

        GLib.timeout_add(1000, run)
        return False

    app._start_runtime = start
    app.main()
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
