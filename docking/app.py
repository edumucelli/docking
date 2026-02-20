"""Application entry point — bootstraps the dock and runs the GTK main loop."""

from __future__ import annotations

import signal
import sys

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib  # noqa: E402

from docking.config import Config
from docking.theme import Theme
from docking.dock_model import DockModel
from docking.dock_renderer import DockRenderer
from docking.dock_window import DockWindow
from docking.launcher import Launcher
from docking.window_tracker import WindowTracker
from docking.autohide import AutoHideController
from docking.dnd import DnDHandler
from docking.menu import MenuHandler


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

    dnd = DnDHandler(window, model, config, renderer, theme)
    window.set_dnd_handler(dnd)

    menu = MenuHandler(window, model, config, tracker)
    window.set_menu_handler(menu)

    # Graceful shutdown on SIGINT/SIGTERM
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGINT, _quit)
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, signal.SIGTERM, _quit)

    window.show_all()
    Gtk.main()


def _quit() -> bool:
    Gtk.main_quit()
    return GLib.SOURCE_REMOVE
