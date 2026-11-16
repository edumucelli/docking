"""Capture GameScope's displayed buffer, including native layer-shell overlays."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

SCREENSHOT = Path("/tmp/gamescope.png")


def capture(target: Path) -> None:
    display = (Path(os.environ["LAB_DIR"]) / "outer-display").read_text().strip()
    SCREENSHOT.unlink(missing_ok=True)
    atom = "GAMESCOPECTRL_DEBUG_REQUEST_SCREENSHOT"
    subprocess.run(
        ["xprop", "-display", display, "-root", "-f", atom, "32c", "-set", atom, "4"],
        env={**os.environ, "XAUTHORITY": ""},
        check=True,
        timeout=5,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        try:
            GdkPixbuf.Pixbuf.new_from_file(str(SCREENSHOT))
        except GLib.Error:
            time.sleep(0.05)
            continue
        shutil.copyfile(SCREENSHOT, target)
        SCREENSHOT.unlink()
        return
    raise TimeoutError("GameScope did not write a complete screen-buffer capture")


if __name__ == "__main__":
    capture(Path(sys.argv[1]))
