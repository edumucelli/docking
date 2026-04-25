"""Application bootstrap for the Docking process.

What this module owns

This file is the narrow runtime bridge between "Python package on disk" and
"running GTK dock on screen". It performs the small set of process-wide steps
that must happen in a specific order before the rest of the dock can operate.

Boot order matters here

Several actions in this module are deliberately early and global:

1. enable ``faulthandler`` so hard crashes still produce Python tracebacks,
2. add the vendor dependency directory when running from packaged installs,
3. initialize gettext before importing modules that declare translated labels,
4. require the GTK version before importing ``gi.repository`` objects,
5. construct the core model / platform adapters / renderer / window objects,
6. hand control to ``Gtk.main()``.

Why this module should stay small

Most behavior does not belong here. Once the initial process wiring is done,
responsibility moves outward:

- configuration and theme loading go through ``docking.core``,
- environment tweaks and window tracking go through ``docking.platform``,
- GTK assembly goes through ``docking.ui.factory`` and related UI modules,
- applet lifecycle is delegated to the model.

That separation is important because entrypoints tend to become accidental
"god modules" when they start absorbing policy decisions. This file should
continue to answer only one question: how does a Docking process start and stop
cleanly?
"""

from __future__ import annotations

import faulthandler
import signal
import sys
from pathlib import Path

# Print Python traceback on SIGSEGV/SIGABRT/SIGFPE to stderr.
# Also dumps on SIGUSR1 for on-demand debugging (kill -USR1 <pid>).
faulthandler.enable()
faulthandler.register(signal.SIGUSR1)

# Add vendor directory for bundled pip dependencies (.deb installs them
# to /usr/lib/docking/vendor to avoid conflicts with system packages).
_VENDOR_DIR = "/usr/lib/docking/vendor"
if Path(_VENDOR_DIR).is_dir():
    sys.path.insert(0, _VENDOR_DIR)

# i18n must init before any module with translatable strings is imported.
from docking.i18n import init as _init_i18n

_init_i18n()

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from docking.core.config import Config
from docking.core.theme import Theme
from docking.ipc import DockItemsService
from docking.platform.environment import apply_tweaks, detect_desktop
from docking.platform.launcher import Launcher
from docking.platform.model import DockModel
from docking.platform.unity import UnityLauncherListener
from docking.platform.window_tracker import WindowTracker
from docking.ui.factory import build_dock_window
from docking.ui.new_year import NewYearGreetingController
from docking.ui.renderer import DockRenderer


def main() -> None:
    """Entry point for the docking application."""
    apply_tweaks(desktop=detect_desktop())

    config = Config.load()
    theme = Theme.load(name=config.theme, icon_size=config.icon_size).with_opacity(
        config.transparency
    )
    launcher = Launcher()
    model = DockModel(config=config, launcher=launcher)
    renderer = DockRenderer()
    tracker = WindowTracker(model=model, launcher=launcher, config=config)
    unity = UnityLauncherListener(model=model)

    window = build_dock_window(
        config=config,
        model=model,
        renderer=renderer,
        theme=theme,
        window_tracker=tracker,
        launcher=launcher,
    )
    items_service = DockItemsService(model=model, window=window)
    new_year = NewYearGreetingController(window=window)

    # Graceful shutdown on SIGINT/SIGTERM
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, _quit)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, _quit)

    try:
        unity.start()
        window.show_all()
        new_year.start()
        GLib.idle_add(_start_runtime, items_service, model)
        Gtk.main()
    finally:
        items_service.stop()
        new_year.stop()
        unity.stop()
        model.stop_applets()


def _start_runtime(items_service: DockItemsService, model: DockModel) -> bool:
    """Start background runtime pieces after the window has been shown."""
    items_service.start()
    model.start_applets()
    return False


def _quit() -> bool:
    Gtk.main_quit()
    return False


if __name__ == "__main__":
    main()
