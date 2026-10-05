"""Choose a role-capable Cinnamon dock display before GTK is imported."""

from __future__ import annotations

import os
import subprocess
import sys

from docking.log import get_logger
from docking.platform.environment import Desktop, detect_desktop, is_wayland_session

log = get_logger(name="backend.cinnamon.startup")

# Opening a display in this process would initialize GTK before the choice can
# be made. Probe in a child using the same interpreter and installed libraries.
# 0: native layer-shell; 42: usable XWayland fallback; 2: neither was verified.
# A distinct success code prevents an import failure (exit 1) selecting X11.
_DISPLAY_PROBE = """
import os
import sys
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk
if not Gtk.init_check()[0]:
    sys.exit(2)
try:
    gi.require_version('GtkLayerShell', '0.1')
    from gi.repository import GtkLayerShell
    if GtkLayerShell.is_supported():
        sys.exit(0)
except (ImportError, ValueError):
    pass
display = Gdk.Display.open(os.environ['DISPLAY'])
sys.exit(42 if display and type(display).__name__ == 'X11Display' else 2)
"""


def prepare_cinnamon_display() -> bool:
    """Prefer layer-shell, otherwise use a verified XWayland dock surface.

    Explicit GTK and session-backend choices win. This only changes the dock's
    GTK transport; backend selection retains native Cinnamon desktop services.
    Failed probes leave normal GTK display selection intact.
    """
    if (
        not detect_desktop() & Desktop.CINNAMON
        or not is_wayland_session()
        or os.environ.get("GDK_BACKEND", "").strip()
        or os.environ.get("DOCKING_BACKEND", "").strip().lower()
        not in {"", "cinnamon", "cinnamon-wayland"}
        or not os.environ.get("DISPLAY", "").strip()
    ):
        return False
    try:
        result = subprocess.run(
            [sys.executable, "-c", _DISPLAY_PROBE],
            env={**os.environ, "GDK_BACKEND": "wayland,x11"},
            capture_output=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        log.debug("Cinnamon display probe could not verify an XWayland fallback")
        return False
    if result.returncode != 42:
        return False
    os.environ["GDK_BACKEND"] = "x11"
    log.info("Using an XWayland dock surface with native Cinnamon services")
    return True
