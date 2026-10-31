"""Capture the private GNOME compositor through its native screenshot interface."""

import os
import sys

from gi.repository import Gio, GLib

if os.environ.get("DOCKING_LAB_PRIVATE_SESSION") != "1":
    raise RuntimeError("GNOME capture requires an isolated compositor session")
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
owned = bus.call_sync(
    "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
    # This test client occupies the allowed media-key name only on the private
    # lab bus. It never replaces a running service (DO_NOT_QUEUE).
    "RequestName", GLib.Variant("(su)", ("org.gnome.SettingsDaemon.MediaKeys", 4)),
    GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, 3000, None,
).unpack()[0]
if owned not in (1, 4):
    raise RuntimeError("Private screenshot client name unavailable")
result = bus.call_sync(
    "org.gnome.Shell.Screenshot", "/org/gnome/Shell/Screenshot",
    "org.gnome.Shell.Screenshot", "Screenshot",
    GLib.Variant("(bbs)", (False, False, sys.argv[1])),
    GLib.VariantType.new("(bs)"), Gio.DBusCallFlags.NO_AUTO_START, 5000, None,
).unpack()
if not result[0]:
    raise RuntimeError("GNOME compositor screenshot failed")
