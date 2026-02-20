"""Application entry point — bootstraps the dock and runs the GTK main loop."""

from __future__ import annotations

import signal
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from docking.core.config import Config
from docking.core.theme import Theme
from docking.platform.model import DockModel
from docking.ui.renderer import DockRenderer
from docking.ui.dock_window import DockWindow
from docking.platform.launcher import Launcher
from docking.platform.window_tracker import WindowTracker
from docking.ui.autohide import AutoHideController
from docking.ui.dnd import DnDHandler
from docking.ui.menu import MenuHandler
from docking.ui.preview import PreviewPopup


def main() -> None:
    """Entry point for the docking application."""
    config = Config.load()
    theme = Theme.load(config.theme)
    launcher = Launcher()
    model = DockModel(config, launcher)
    renderer = DockRenderer()
    tracker = WindowTracker(model, launcher)

    window = DockWindow(config, model, renderer, theme, tracker)

    autohide = AutoHideController(window, config)
    window.set_autohide_controller(autohide)

    dnd = DnDHandler(window, model, config, renderer, theme, launcher)
    window.set_dnd_handler(dnd)

    menu = MenuHandler(window, model, config, tracker)
    window.set_menu_handler(menu)

    preview = PreviewPopup(tracker)
    preview.set_autohide(autohide)
    window.set_preview_popup(preview)

    # Graceful shutdown on SIGINT/SIGTERM
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, _quit)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, _quit)

    window.show_all()
    Gtk.main()


def _quit() -> bool:
    Gtk.main_quit()
    return GLib.SOURCE_REMOVE
