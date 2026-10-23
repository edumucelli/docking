"""Verify live Wayland toplevel events reach the installed Docking D-Bus API."""

from __future__ import annotations

import subprocess
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

APP_ID = "org.docking.PackageSmoke"

if "--window" in sys.argv:
    GLib.set_prgname(APP_ID)
    app = Gtk.Application(application_id=APP_ID)

    def activate(application):
        window = Gtk.ApplicationWindow(application=application)
        window.set_title("Docking package Wayland probe")
        window.set_default_size(320, 200)
        window.show_all()

    app.connect("activate", activate)
    raise SystemExit(app.run([]))

probe = subprocess.Popen([sys.executable, __file__, "--window"])
try:
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    for _attempt in range(60):
        if probe.poll() is not None:
            raise RuntimeError("The Wayland probe window exited before discovery")
        reply = bus.call_sync(
            "org.docking.Docking",
            "/org/docking/Docking",
            "org.docking.Docking.Items1",
            "ListTransientIds",
            None,
            None,
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        )
        if f"{APP_ID}.desktop" in reply.unpack()[0]:
            print(
                "Docking discovered a native Wayland toplevel through its live protocol"
            )
            break
        time.sleep(0.5)
    else:
        raise RuntimeError(
            f"Docking did not discover the Wayland probe: {reply.unpack()}"
        )
finally:
    probe.terminate()
    try:
        probe.wait(timeout=5)
    except subprocess.TimeoutExpired:
        probe.kill()
        probe.wait()
