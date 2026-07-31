# Author: Eduardo Mucelli Rezende Oliveira
# E-mail: edumucelli@gmail.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.

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
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

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
from docking.log import get_logger, with_context

_init_i18n()

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

# Give GTK / Mutter a stable program name so the GNOME Shell extension
# can find the dock window on Wayland (where WM_CLASS is not forwarded).
GLib.set_prgname("Docking")

from docking.applets.services import AppletServices
from docking.core.config import Config
from docking.core.theme import Theme
from docking.ipc import DockItemsService
from docking.platform.backends.selection import create_session_backend
from docking.platform.environment import apply_tweaks, detect_desktop
from docking.platform.launcher import Launcher
from docking.platform.model import DockModel
from docking.platform.status_notifier import StatusNotifierNotificationBridge
from docking.platform.unity import UnityLauncherListener
from docking.ui.factory import build_dock_window
from docking.ui.renderer import DockRenderer

if TYPE_CHECKING:
    from docking.platform.backends.base import SessionBackend

_FORCE_QUIT_SOURCE_ID = 0


log = with_context(get_logger(name="app"), action="start_runtime")


def main() -> None:
    """Entry point for the docking application."""
    apply_tweaks(desktop=detect_desktop())

    config = Config.load()
    theme = Theme.load(name=config.theme, icon_size=config.icon_size).with_opacity(
        config.transparency
    )
    launcher = Launcher()
    model = DockModel(
        config=config,
        launcher=launcher,
        applet_services=AppletServices(),
    )
    renderer = DockRenderer()
    backend = create_session_backend(config=config, launcher=launcher, model=model)
    model.set_applet_services(
        AppletServices(
            desktop_actions=backend.desktop_actions,
            workspaces=backend.workspaces,
            window_picker=backend.window_picker,
            idle=backend.idle,
            screen_capture=backend.screen_capture,
        )
    )
    unity = UnityLauncherListener(model=model)
    status_notifications = StatusNotifierNotificationBridge(model=model)

    ui = build_dock_window(
        config=config,
        model=model,
        renderer=renderer,
        theme=theme,
        window_tracker=backend.windows,
        preview_service=backend.previews,
        surface_service=backend.surface,
        visibility_service=backend.visibility,
        session_backend=backend,
        launcher=launcher,
    )
    window = ui.window
    items_service = DockItemsService(model=model, window=window)
    model.set_applet_services(
        AppletServices(
            desktop_actions=backend.desktop_actions,
            workspaces=backend.workspaces,
            window_picker=backend.window_picker,
            idle=backend.idle,
            screen_capture=backend.screen_capture,
            search=ui.search,
        )
    )

    # Graceful shutdown on SIGINT/SIGTERM
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, _quit)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, _quit)

    try:
        status_notifications.start()
        unity.start()
        window.show_all()
        ui.start()
        GLib.idle_add(_start_runtime, items_service, model, backend)
        Gtk.main()
    finally:
        items_service.stop()
        status_notifications.stop()
        ui.stop()
        unity.stop()
        model.stop_applets()
        backend.stop()


def _start_runtime(
    items_service: DockItemsService,
    model: DockModel,
    backend: SessionBackend,
) -> bool:
    """Start background runtime pieces after the window has been shown."""
    _start_runtime_stage(name="backend", start=backend.start)
    _start_runtime_stage(name="items_service", start=items_service.start)
    _start_runtime_stage(name="applets", start=model.start_applets)
    return False


def _start_runtime_stage(*, name: str, start: Callable[[], object]) -> None:
    try:
        start()
    except Exception:
        log.exception("Failed to start runtime stage: %s", name)


def _quit() -> bool:
    global _FORCE_QUIT_SOURCE_ID

    # Shutdown is intentionally two-stage.  Give GTK, applets, IPC services,
    # and backend adapters a normal main-loop exit first; if something wedges
    # during teardown, force the process out after a short grace period.
    Gtk.main_quit()
    if _FORCE_QUIT_SOURCE_ID == 0:
        _FORCE_QUIT_SOURCE_ID = GLib.timeout_add_seconds(3, _force_quit)
    return False


def _force_quit() -> bool:
    os._exit(0)


if __name__ == "__main__":
    main()
