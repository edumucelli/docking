"""Check real Windows GPU capture with animated, occluded and resized windows.

This owns its GTK fixtures and compares their known source colors with capture
pixels. A covering window appears in the desktop PNG while the source continues
updating in the captured images. No existing application windows are closed.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import subprocess
import sys
import time
from collections import Counter
from ctypes import wintypes
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fixture", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cover", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("This check requires native Windows")
    if not (args.fixture or args.cover) and args.output is None:
        parser.error("--output is required")
    repo = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo))
    from docking.launcher import prepare_display

    prepare_display()

    if args.fixture or args.cover:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import GLib, Gtk

        covered = args.cover
        window = Gtk.Window(
            title="Docking cover fixture" if covered else "Docking capture fixture"
        )
        window.set_default_size(640, 360)
        window.move(160, 100)
        area = Gtk.DrawingArea()
        counter = [0]

        def draw(widget, cr):
            w, h = widget.get_allocated_width(), widget.get_allocated_height()
            colors = (
                [(1, 0, 1)] * 4
                if covered
                else [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0)]
            )
            for index, color in enumerate(colors):
                cr.set_source_rgb(*color)
                cr.rectangle((index % 2) * w / 2, (index // 2) * h / 2, w / 2, h / 2)
                cr.fill()
            cr.set_source_rgb(1, 1, 1)
            cr.set_font_size(30)
            cr.move_to(20, 50)
            cr.show_text("Cover" if covered else "Native frame " + str(counter[0]))

        def tick():
            counter[0] += 1
            area.queue_draw()
            return True

        area.connect("draw", draw)
        window.add(area)
        window.connect("destroy", Gtk.main_quit)
        window.show_all()
        GLib.timeout_add(250, tick)
        GLib.timeout_add_seconds(60, lambda: (window.destroy(), False)[1])
        Gtk.main()
    else:
        from PIL import Image, ImageGrab

        from docking.platform.backends.windows.capture import (
            WindowsCaptureDevice,
            WindowsCaptureStream,
        )
        from docking.platform.backends.windows.windows import Win32WindowAPI

        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.PostMessageW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user.SetWindowPos.argtypes = (
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        source = subprocess.Popen(
            [sys.executable, __file__, "--fixture"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        cover = None
        native = Win32WindowAPI()
        device = stream = None
        handles = []
        report = {}

        def find(pid):
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                row = next((w for w in native.windows() if w.pid == pid), None)
                if row is not None:
                    handles.append(int(row.id.value))
                    return row
                time.sleep(0.1)
            raise AssertionError("No fixture window appeared")

        def capture(label, minimum_width=0):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                pixels = stream.read(timeout=0.5)
                if pixels is not None and pixels.width >= minimum_width:
                    image = Image.frombytes(
                        "RGBA", (pixels.width, pixels.height), pixels.pixels
                    )
                    image.save(output / (label + ".png"))
                    colors = Counter(
                        pixels.pixels[index : index + 4]
                        for index in range(0, len(pixels.pixels), 4)
                    )
                    counts = {
                        str(rgb): colors[bytes((*rgb, 255))]
                        for rgb in [
                            (255, 0, 0),
                            (0, 255, 0),
                            (0, 0, 255),
                            (255, 255, 0),
                            (255, 0, 255),
                        ]
                    }
                    assert all(
                        colors[bytes((*rgb, 255))] > 20000
                        for rgb in [
                            (255, 0, 0),
                            (0, 255, 0),
                            (0, 0, 255),
                            (255, 255, 0),
                        ]
                    ), counts
                    report[label] = {
                        "width": pixels.width,
                        "height": pixels.height,
                        "colors": counts,
                    }
                    return pixels.pixels
            raise AssertionError("No native capture frame arrived")

        try:
            window = find(source.pid)
            time.sleep(1)
            device = WindowsCaptureDevice()
            stream = WindowsCaptureStream(device, int(window.id.value), source.pid)
            capture("visible")
            cover = subprocess.Popen(
                [sys.executable, __file__, "--cover"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            find(cover.pid)
            time.sleep(1)
            ImageGrab.grab(all_screens=True).save(output / "covered-desktop.png")
            first = capture("occluded")
            time.sleep(0.7)
            # Drain pre-cover frames and observe the source's own animated frame count.
            latest = first
            for _ in range(6):
                latest = capture("occluded-live")
                time.sleep(0.2)
            report["source_updates_while_occluded"] = first != latest
            assert report["source_updates_while_occluded"]
            assert user.SetWindowPos(int(window.id.value), None, 0, 0, 820, 480, 0x16)
            capture("resized", minimum_width=800)
            user.PostMessageW(int(window.id.value), 0x10, 0, 0)
            assert source.wait(timeout=10) == 0
            assert stream.read() is None
            report["closed_source_rejected"] = True
            report["ok"] = True
        except BaseException as error:
            report["ok"] = False
            report["error"] = repr(error)
            raise
        finally:
            if stream is not None:
                stream.close()
            if device is not None:
                device.close()
            for hwnd in handles:
                user.PostMessageW(hwnd, 0x10, 0, 0)
            for child in [source, cover]:
                if child is None:
                    continue
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait(timeout=5)
            (output / "result.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
