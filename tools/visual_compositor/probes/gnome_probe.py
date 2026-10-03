"""Query the lab-only observer inside the private GNOME Shell session."""

import sys

from gi.repository import Gio, GLib

method = {
    "windows": "ListWindows",
    "workspaces": "ListWorkspaces",
    "geometry": "GetGeometry",
}[sys.argv[1]]
result = Gio.bus_get_sync(Gio.BusType.SESSION, None).call_sync(
    "org.docking.VisualLab.Gnome",
    "/org/docking/VisualLab/Gnome",
    "org.docking.VisualLab.Gnome1",
    method,
    None,
    GLib.VariantType.new("(s)"),
    Gio.DBusCallFlags.NONE,
    5000,
    None,
)
print(result.unpack()[0])
